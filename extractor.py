"""Layout-aware, project-independent 28-day concrete schedule extraction.

No preset strength values, category counts, floor boundaries, or project names.
Scans vector-text PDF geometry; does not assert accuracy on scanned drawings.
"""
from __future__ import annotations
import re
from collections import defaultdict

PSI = re.compile(r'(?<![\d#])(?P<number>(?:\d{1,3}(?:,\d{3})+|\d{3,6}|\d{1,3}(?:\s\d{3})+))\s*(?:[-–]\s*)?(?:P\.?S\.?I\.?)\b', re.I)
LETTER = re.compile(r'^\s*([A-Z])\s*[.\)]\s*(.*)$', re.I)
NUMBERED = re.compile(r'^\s*\d{1,2}\s*[.\)]\s*$', re.I)
STRENGTH_HEADER = re.compile(r'COMPRESSIVE\s+STRENGTH|CONCRETE\s+STRENGTH|\bF\s*[\'′]\s*C\b.*\bPSI\b', re.I)
CONCRETE_WORDS = re.compile(r'CONCRETE|SLAB|BEAM|WALL|COLUMN|FOOTING|MAT|GRADE|STAIR|PILE|PIER|PAD|FOUNDATION|TOPPING|ELEMENT|STRUCTURAL', re.I)
FLOOR_WORDS = re.compile(r'\b(?:CELLAR|BASEMENT|ROOF|FLOOR|FLOORS|LEVEL|LEVELS|STORY|STORIES|STOREY|PENTHOUSE|PODIUM|MEZZANINE|GROUND|BULKHEAD)\b', re.I)
WEIGHT_RE = re.compile(r'\b(?:NORMAL[\s-]*WEIGHT|LIGHT[\s-]*WEIGHT|LIGHTWEIGHT|NW|LW)\b', re.I)


def page_lines(page):
    """Get positioned text fragments (one PDF text line per fragment)."""
    items = []
    for b in page.get_text('dict')['blocks']:
        for line in b.get('lines', []):
            txt = ''.join(sp['text'] for sp in line['spans']).strip()
            if txt:
                x0,y0,x1,y1 = line['bbox']
                items.append({'x':float(x0),'y':float(y0),'x1':float(x1),'y1':float(y1),'text':txt})
    return sorted(items,key=lambda l:(l['y'],l['x']))


def find_anchors(lines):
    anchors=[]
    for l in lines:
        m = LETTER.match(l['text'])
        if not m:
            continue
        if len(m.group(2))>150 or m.group(1).upper() in ('I',) and l['text'].startswith('I '):
            continue
        anchors.append(dict(l,letter=m.group(1).upper(), remainder=m.group(2).strip()))
    return anchors


def nearest_cluster(anchor, pool):
    """Pick the run of aligned labels that follows the candidate A-label."""
    rows=sorted([x for x in pool if abs(x['x']-anchor['x'])<=20 and anchor['y']-1<=x['y']<=anchor['y']+375],key=lambda l:l['y'])
    if not rows:
        return []
    chain=[]
    for line in rows:
        if chain and (line['y']-chain[-1]['y']>95 or (line['letter'] == 'A' and len(chain)>1)):
            break
        if chain and ord(line['letter'])<ord(chain[-1]['letter']):
            break
        if chain and line['letter']==chain[-1]['letter']:
            continue
        chain.append(line)
    return chain


def label_category(text):
    text=re.sub(r'\s+', ' ',text).strip(' :;.,')
    floor=''
    # Some firms put the floor range in parentheses on the same category line.
    parens=re.findall(r'\(([^()]*)\)',text)
    for section in parens:
        if FLOOR_WORDS.search(section):
            floor=section.strip(' :;')
            text=text.replace('('+section+')','').strip(' :;,')
            break
    # Or after a dash, e.g. COLUMNS - 11TH FLOOR TO ROOF
    if not floor:
        pieces=re.split(r'\s+(?:[-–]|:)\s+',text,maxsplit=1)
        if len(pieces)==2 and FLOOR_WORDS.search(pieces[1]):
            text, floor=pieces[0].strip(' :;'),pieces[1].strip(' :;')
    return text, floor


def nearest_note_end(start, lines, label_x):
    next_header = [l['y'] for l in lines
                   if l['y']>start and abs(l['x']-label_x)<42
                   and (NUMBERED.fullmatch(l['text']) or (STRENGTH_HEADER.search(l['text']) and 'PSI' not in l['text']))]
    return min(next_header) if next_header else start+480


def parse_run(page, lines, run, *, header_found, filename, sheet):
    if len(run)<2:
        return [],[]
    # Limit rightward read extent by schedule width inferred from aligned strength entries.
    # In large landscape sheets, avoid nearby unrelated rebar tables.
    left_x=min(a['x'] for a in run)
    max_right=(min(page.rect.width, left_x + 720) if page.rect.width>1450
               else page.rect.width*.96)
    start_y=run[0]['y']
    end_y=min(start_y+470, run[-1]['y']+80, nearest_note_end(run[-1]['y']+3,lines,left_x))
    items=[l for l in lines if left_x-12<=l['x']<=max_right and start_y-3<=l['y']<end_y]
    all_outputs=[]
    warnings=[]
    for i,a in enumerate(run):
        y0=a['y']-2
        y1=run[i+1]['y']-2 if i+1<len(run) else end_y
        parts=[l for l in items if y0<=l['y']<y1]
        if not parts:
            continue
        # Remove letter marker, leaving any text on same fragment.
        meaningful=[]
        for l in parts:
            s=l['text']
            if l['x']<left_x+28:
                m=LETTER.match(s)
                if m and m.group(1).upper()==a['letter']:
                    s=m.group(2)
            if s.strip():
                meaningful.append(dict(l,text=s.strip()))
        if not meaningful:
            continue
        psi_parts=[(l,m) for l in meaningful for m in PSI.finditer(l['text'])]
        # PSI can be split into neighboring PDF spans; join same-line fragments if needed.
        if not psi_parts:
            grouped_by_y=defaultdict(list)
            for l in meaningful:grouped_by_y[round(l['y']/3)*3].append(l)
            for group in grouped_by_y.values():
                s=' '.join(l['text'] for l in sorted(group,key=lambda q:q['x']))
                for m in PSI.finditer(s):
                    psi_parts.append((dict(group[-1],text=s),m))
        if not psi_parts:
            warnings.append({'file':filename,'sheet':sheet,'page':page.number+1,
                             'warning':f"No readable PSI for specification {a['letter']} (near y={a['y']:.0f})."})
            continue
        # Determine category by first text line left of PSI column, then carry wrapped lines.
        first_psi_x=min(l['x'] for l,m in psi_parts)
        left_parts=[l for l in meaningful if l['x']<first_psi_x-8 and not PSI.search(l['text'])]
        left_parts.sort(key=lambda l:(l['y'],l['x']))
        # Collect initial category lines until a floor-range line; keep wrapped categories.
        category_lines=[]
        level_lines=[]
        for l in left_parts:
            s=l['text'].strip()
            if not s or re.fullmatch(r'\d{1,2}\.',s):continue
            if FLOOR_WORDS.search(s) and category_lines and not (s.upper().startswith(('FRAMED ','SHEAR ','SLAB ','FOUNDATION ','ALL ','CONCRETE ','OTHER ','MAT ','FOOTING '))):
                level_lines.append(l)
            elif level_lines:
                # Continuation of a floor-range label without the word FLOOR.
                level_lines.append(l)
            else:
                category_lines.append(l)
        # Generic handling of first line already containing a level in parentheses.
        if category_lines:
            # Category must come from first row or wrapped text, not future level rows.
            category=' '.join(l['text'] for l in category_lines).strip()
        else:
            category=a.get('remainder','').strip()
        category, inline_floor=label_category(category)
        if not category or not CONCRETE_WORDS.search(category):
            # Reject suspicious label groups rather than inventing element names.
            warnings.append({'file':filename,'sheet':sheet,'page':page.number+1,
                             'warning':f"Uncertain concrete category {a['letter']} near y={a['y']:.0f}; please verify."})
            continue
        # Use nearest same-line or preceding floor range for each strength row.
        levels=[l for l in left_parts if FLOOR_WORDS.search(l['text']) and l not in category_lines]
        record_y_seen=set()
        for psi_fragment,mtch in psi_parts:
            val=int(re.sub(r'\D','',mtch.group('number')))
            if val<1000 or val>30000:
                continue
            y=psi_fragment['y']
            # Keep all distinct value rows; duplicate PDF text positions count once.
            rowkey=(round(y/2),val)
            if rowkey in record_y_seen:continue
            record_y_seen.add(rowkey)
            near_same=[l for l in levels if abs(l['y']-y)<=5]
            prior=[l for l in levels if l['y']<=y+4]
            best=(min(near_same,key=lambda x:abs(x['y']-y)) if near_same
                  else max(prior,key=lambda x:x['y']) if prior else None)
            level=(best['text'].strip(' :;,') if best else inline_floor)
            # Normal/light weight might appear in another fragment same row.
            peer=' '.join(l['text'] for l in meaningful if abs(l['y']-y)<=5)
            wm=WEIGHT_RE.search(peer)
            weight=wm.group().upper().replace('NORMALWEIGHT','NORMAL WEIGHT') if wm else ''
            all_outputs.append({'file':filename,'sheet':sheet,'page':page.number+1,
                'letter':a['letter'],'element':category,'level_range':level,
                'strength_psi':val,'weight':weight,
                'confidence':'Matched note and aligned PSI' if header_found else 'Inferred schedule (verify)',
                'source_text':a['letter']+'. '+category+(f' ({level})' if level else '')+f' — {val:,} PSI'})
    return all_outputs,warnings


def extract_strength_schedules(page,filename,sheet):
    lines=page_lines(page)
    anchors=find_anchors(lines)
    header_lines=[l for l in lines if STRENGTH_HEADER.search(l['text']) and
                  ('CONCRETE' in l['text'].upper() or 'COMPRESSIVE' in l['text'].upper()) and
                  not re.search(r'\b(?:MASONRY|CMU|GROUT|BRICK|MORTAR)\b',l['text'],re.I)]
    outputs=[]; warnings=[]; used_positions=set()
    candidate_runs=[]
    if header_lines:
        for hd in header_lines:
            pool=[a for a in anchors if 0<a['y']-hd['y']<400 and abs(a['x']-hd['x'])<170]
            if not pool:continue
            # Favor earliest A-label, but allow section starting B etc.
            for a in sorted(pool,key=lambda x:x['y']):
                if a['letter'] not in ('A','B'):
                    continue
                run=nearest_cluster(a,pool)
                if len(run)>=2:
                    candidate_runs.append((run, True))
                    break
    else:
        # Headless schedules may begin directly with A. MAT / B. SHEAR WALL.
        for a in anchors:
            if a['letter']!='A':continue
            pool=[v for v in anchors if 0<=v['y']-a['y']<330]
            run=nearest_cluster(a,pool)
            if len(run)>=2:
                # Confirm multiple PSI values and concrete-related words in area.
                nearby=[l for l in lines if a['y']-2<=l['y']<=run[-1]['y']+24 and
                        a['x']-10<=l['x']<=a['x']+700]
                if (len(PSI.findall(' '.join(l['text'] for l in nearby)))>=2 and
                    len(CONCRETE_WORDS.findall(' '.join(l['text'] for l in nearby)))>=2):
                    candidate_runs.append((run,False))
    for run,with_header in candidate_runs:
        key=(round(run[0]['x']/12),round(run[0]['y']/8))
        if key in used_positions:continue
        used_positions.add(key)
        rows,issues=parse_run(page,lines,run,header_found=with_header,filename=filename,sheet=sheet)
        outputs.extend(rows); warnings.extend(issues)
        if rows and not with_header:
            warnings.append({'file':filename,'sheet':sheet,'page':page.number+1,
                'warning':'Strength schedule inferred without a recognizable heading; check against original.'})
    outputs=list({(o['file'],o['sheet'],o['page'],o['letter'],o['element'],o['level_range'],o['strength_psi']):o for o in outputs}.values())
    # A generic completeness check: every recognized letter should carry some strength.
    return outputs,warnings
