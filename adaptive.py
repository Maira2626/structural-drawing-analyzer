"""Geometry-first, value-agnostic parsing of PDF concrete-strength schedules.

A parsed PDF is not the same as a drawing: this module records uncertainty and
unmatched strength notes. It never supplies missing values from a template.
"""
from __future__ import annotations
import re
from collections import defaultdict

LETTER = re.compile(r'^\s*([A-Z])\s*[.\)](?:\s+|$)(.*)$', re.I)
PSI = re.compile(r'(?<![\w#])((?:\d{1,3}(?:[,. ]\d{3})+|\d{4,6}))\s*[-–]?\s*P\.?S\.?I\.?(?![a-z])', re.I)
PSI_ONLY = re.compile(r'^\s*(?:\d{1,3}(?:[,. ]\d{3})+|\d{4,6})\s*[-–]?\s*P\.?S\.?I\.?(?:\s*[,;]?\s*(?:NORMAL|LIGHT)[- ]?WEIGHT)?\s*$',re.I)
FLOOR = re.compile(r'\b(?:CELLAR|BASEMENT|ROOF|FLOOR|LEVEL|STORY|STOREY|PENTHOUSE|PODIUM|MEZZANINE|GROUND|BULKHEAD|FIRST|SECOND|THIRD|\d+(?:ST|ND|RD|TH))\b',re.I)
ELEMENT = re.compile(r'\b(?:CONC\.?|CONCRETE|SLABS?|BEAMS?|WALLS?|COLUMNS?|FOOTINGS?|MATS?|STAIRS?|PIERS?|PILE|FOUNDATION|TOPPING|PADS?|GRADE|STRUCTURAL|FLAT PLATE|CAISSONS?|CORE)\b',re.I)
STRENGTH_HEAD = re.compile(r'(?:COMPRESSIVE\s+STRENGTH|CONCRETE\s+STRENGTH|STRENGTH\s+OF\s+CONCRETE|\bF\s*[\'′]?C\b)',re.I)
WEIGHT = re.compile(r'\b(?:NORMAL\s*[- ]?\s*WEIGHT|LIGHT\s*[- ]?\s*WEIGHT)\b',re.I)


def _lines(page):
    out=[]
    for b in page.get_text('dict').get('blocks',[]):
        for l in b.get('lines',[]):
            raw=''.join(s['text'] for s in l.get('spans',[])).strip()
            if raw:
                x0,y0,x1,y1=l['bbox']
                out.append({'x':float(x0),'y':float(y0),'x1':float(x1),'y1':float(y1),'text':raw})
    return sorted(out,key=lambda r:(r['y'],r['x']))


def _category_and_floor(s):
    s=re.sub(r'\s+',' ',s).strip(' :;.\t')
    floor=''
    for a in re.findall(r'\(([^)]+)\)',s):
        if FLOOR.search(a):
            floor=a.strip(' .:')
            s=s.replace('('+a+')','').strip(' :;,.')
            break
    if not floor and ' - ' in s:
        u,v=s.rsplit(' - ',1)
        if FLOOR.search(v):
            s,floor=u.strip(' :;'),v.strip(' :;')
    return s,floor


def _floor_only(s):
    s=s.strip(' :;,.')
    if not FLOOR.search(s): return False
    if re.match(r'^(?:\d+(?:ST|ND|RD|TH)?|CELLAR|BASEMENT|GROUND|ROOF|PENTHOUSE|PODIUM|MEZZANINE|FROM|LEVEL|FLOOR|STORY|B\d|L\d)\b',s,re.I):return True
    if re.search(r'\b(?:TO|THROUGH|THRU|UP TO|ABOVE|BELOW)\b',s,re.I) and not re.match(r'^(?:SLAB|WALL|BEAM|COLUMN|SHEAR|FOUNDATION|FRAMED|MAT|GRADE|ALL|OTHER|CONCRETE)',s,re.I): return True
    return False


def _clusters(anchors, scale):
    """Find aligned sequential letter runs without fixed schedule sizes."""
    seen=set();out=[]
    for a in anchors:
        if a['letter']!='A':continue
        run=[]
        for v in anchors:
            if abs(v['x']-a['x'])>max(18,scale*.012):continue
            if not (a['y']-2 <=v['y']<=a['y']+max(350,scale*.52)):continue
            if run and v['y']-run[-1]['y']>max(95,scale*.085):break
            if not run and v['letter']=='A':run.append(v)
            elif run and ord(v['letter'])==ord(run[-1]['letter'])+1:run.append(v)
            elif run and ord(v['letter'])<ord(run[-1]['letter']):break
        if len(run)>=2:
            k=(round(run[0]['x']/6),round(run[0]['y']/6))
            if k not in seen:seen.add(k);out.append(run)
    return out


def _normal_fragments(page):
    """Join fragments from same visual line only when needed for split-value text."""
    lines=_lines(page)
    return lines


def _pick_value_column(run, fragments, width):
    y0,y1=run[0]['y']-4,run[-1]['y']+max(38,width*.016)
    x0=run[0]['x']
    pts=[]
    for f in fragments:
        if not(y0<=f['y']<=y1 and x0-4<=f['x']<=min(width*.98,x0+max(1250,width*.66))):continue
        if PSI.search(f['text']):pts.append(f)
    # Score x positions based on repeated strengths and proximity to first labels.
    if not pts:return None
    groups=defaultdict(list)
    for p in pts:groups[round(p['x']/max(16,width*.012))].append(p)
    candidates=[g for g in groups.values() if len(g)>=max(2,min(len(run)-1,4))]
    if candidates:
        group=min(candidates,key=lambda g:sum(p['x'] for p in g)/len(g))
    else:
        group=min(groups.values(),key=lambda g:(-len(g),sum(p['x'] for p in g)/len(g)))
    return sum(p['x'] for p in group)/len(group)


def extract_adaptive_strengths(page,filename,sheet):
    lines=_normal_fragments(page)
    anchors=[]
    for l in lines:
        m=LETTER.match(l['text'])
        if m:
            anchors.append(dict(l,letter=m.group(1).upper(),initial=m.group(2).strip()))
    runs=_clusters(anchors,page.rect.height)
    results=[];warn=[]; candidate_keys=[]
    headers=[l for l in lines if STRENGTH_HEAD.search(l['text']) and not re.search(r'MASONRY|CMU|GROUT|MORTAR',l['text'],re.I)]
    for run in runs:
        value_x=_pick_value_column(run,lines,page.rect.width)
        if value_x is None:continue
        x0=run[0]['x']; y0=run[0]['y'];y2=run[-1]['y']
        nearby_headers=[h for h in headers if 0<y0-h['y']<max(170,page.rect.height*.22) and abs(h['x']-x0)<max(250,page.rect.width*.15)]
        head=bool(nearby_headers)
        # Need at least two relevant categories to avoid spurious lettered lists.
        region=[l for l in lines if y0-3<=l['y']<=y2+max(40,page.rect.height*.035) and x0-6<=l['x']<=value_x+max(220,page.rect.width*.1)]
        token=' '.join(l['text'] for l in region)
        if len(ELEMENT.findall(token))<2 or len(PSI.findall(token))<2: continue
        candidate_keys.append((x0,y0))
        run_results=[];run_warnings=[]
        for i,a in enumerate(run):
            row_start=a['y']-max(3,page.rect.height*.002)
            row_end=run[i+1]['y']-max(3,page.rect.height*.002) if i+1<len(run) else y2+max(35,page.rect.height*.042)
            if i==len(run)-1:
                next_boundary=[l['y'] for l in lines if l['y']>a['y']+4 and abs(l['x']-x0)<max(35,page.rect.width*.015) and re.fullmatch(r'\d{1,2}\s*[.)]',l['text'])]
                if next_boundary:row_end=min(row_end,min(next_boundary)-2)
            frag=[dict(l) for l in region if row_start<=l['y']<row_end]
            if not frag:continue
            vals=[]
            for f in frag:
                # Do not parse the reinforcement table in an adjacent column.
                if f['x']>value_x+max(175,page.rect.width*.065):continue
                for match in PSI.finditer(f['text']):
                    v=int(re.sub(r'\D','',match.group(1)))
                    if 1000<=v<=40000:vals.append((v,f))
            if not vals:
                run_warnings.append({'file':filename,'sheet':sheet,'page':page.number+1,'warning':f'Specification {a["letter"]} has no readable psi value; check original.'})
                continue
            # All category text is left of value column; use first lines and wraps.
            desc=[];levels=[]
            for f in sorted(frag,key=lambda x:(x['y'],x['x'])):
                if f['x']>value_x-8 and f['x']>x0+70:continue
                s=f['text'].strip()
                m=LETTER.match(s)
                if m and f['x']<x0+max(28,page.rect.width*.016):s=m.group(2).strip()
                mpsi=PSI.search(s)
                if mpsi:s=s[:mpsi.start()].strip(' :;,')
                if not s or re.fullmatch(r'[A-Z][.)]',s) or re.fullmatch(r'\d{1,2}[.)]',s):continue
                if _floor_only(s):levels.append(dict(f,text=s))
                else:desc.append(dict(f,text=s))
            raw=' '.join(p['text'] for p in desc)
            raw=re.sub(r'\s+',' ',raw).strip()
            category,inline_level=_category_and_floor(raw)
            if not ELEMENT.search(category) or len(category)>230:
                run_warnings.append({'file':filename,'sheet':sheet,'page':page.number+1,'warning':f'Uncertain description for specification {a["letter"]}: {category[:80]}. Check original.'})
                continue
            lastseen=set()
            for value,f in vals:
                k=(value,round(f['y']/max(2,page.rect.height*.002)))
                if k in lastseen:continue
                lastseen.add(k)
                same=[lv for lv in levels if abs(lv['y']-f['y'])<max(6,page.rect.height*.006)]
                previous=[lv for lv in levels if lv['y']<=f['y']+max(3,page.rect.height*.003)]
                level=(min(same,key=lambda l:abs(l['y']-f['y'])) if same else max(previous,key=lambda l:l['y']) if previous else None)
                value_level=level['text'].strip(' :;,') if level else inline_level
                adjacent=' '.join(p['text'] for p in frag if abs(p['y']-f['y'])<max(5,page.rect.height*.005))
                weight=WEIGHT.search(adjacent)
                run_results.append({'file':filename,'sheet':sheet,'page':page.number+1,'letter':a['letter'],
                     'element':category,'level_range':value_level,'strength_psi':value,
                     'weight':weight.group().upper() if weight else '',
                     'confidence':'Heading + source geometry' if head else 'Inferred geometry — verify',
                     'source_text':f'{a["letter"]}. {category}' + (f' ({value_level})' if value_level else '')+f' — {value:,} PSI'})
        if len({r["letter"] for r in run_results})<2:
            continue
        results.extend(run_results)
        warn.extend(run_warnings)
        if not head:
            warn.append({'file':filename,'sheet':sheet,'page':page.number+1,'warning':'Concrete-strength schedule recognized without a clear heading; verify source.'})
    results=list({(r['file'],r['sheet'],r['page'],r['letter'],r['element'],r['level_range'],r['strength_psi']):r for r in results}.values())
    return results,warn


def uncovered_psi_notes(page, filename, sheet, records):
    """Read-only snippets to inspect things parser could not confidently assign."""
    lines=_lines(page)
    associated_y={round(r.get('_y',-100)) for r in records}
    out=[]
    for line in lines:
        for m in PSI.finditer(line['text']):
            v=int(re.sub(r'\D','',m.group(1)))
            if not(1000<=v<=40000):continue
            # Only show context-rich notes; table headings are too noisy.
            nearby=[q for q in lines if abs(q['y']-line['y'])<7 and 0<=q['x']<=line['x']+250]
            txt=' '.join(q['text'] for q in sorted(nearby,key=lambda q:q['x']))
            if not ELEMENT.search(txt) and not STRENGTH_HEAD.search(txt):continue
            if re.search(r'\bLAP SPLICE|DEVELOPMENT LENGTH|LCS|LTS|LDT\b',txt,re.I):continue
            out.append({'file':filename,'sheet':sheet,'page':page.number+1,'psi':v,'text':txt[:250]})
    return list({(r['file'],r['sheet'],r['page'],r['psi'],r['text']):r for r in out}.values())

COVER_HEAD = re.compile(r'(?:MINIMUM|MIN\.?|CLEAR|REQUIRED)?\s*(?:CONCRETE\s+)?COVER\s+(?:SHALL\s+BE|REQUIREMENTS?|SCHEDULE|TO\s+REINFORCEMENT)',re.I)
INCH = re.compile(r'(?<![\d])(?:\d+(?:[- ]\d+/\d+|/\d+)?|\d*\.\d+)\s*(?:INCH(?:ES)?|IN\.?\b|MM\b|["″])',re.I)


def extract_adaptive_covers(page, filename, sheet):
    """Extract schedule rows only when located beneath a cover heading."""
    lines=_lines(page)
    anchors=[]
    for l in lines:
        m=LETTER.match(l['text'])
        if m: anchors.append(dict(l,letter=m.group(1).upper(),initial=m.group(2).strip()))
    headers=[l for l in lines if COVER_HEAD.search(l['text'])]
    rows=[];issues=[]
    for header in headers:
        pool=[a for a in anchors if header['y']-1<a['y']<header['y']+max(320,page.rect.height*.48) and abs(a['x']-header['x'])<max(230,page.rect.width*.15)]
        if not pool: continue
        candidates=_clusters(pool,page.rect.height)
        if not candidates:continue
        run=min(candidates,key=lambda v:abs(v[0]['y']-header['y']))
        # Constrain to the vicinity of header; prevent importing unrelated tables.
        if run[0]['y']-header['y']>max(125,page.rect.height*.13):continue
        values=[l for l in lines if run[0]['y']-2<=l['y']<=run[-1]['y']+45 and l['x']>run[0]['x']
                and l['x']<min(page.rect.width,run[0]['x']+max(850,page.rect.width*.5)) and INCH.search(l['text'])]
        if len(values)<2:continue
        vals_by_x=defaultdict(list)
        for v in values: vals_by_x[round(v['x']/max(16,page.rect.width*.01))].append(v)
        group=max(vals_by_x.values(),key=lambda g:(len(g),-min(l['x'] for l in g)))
        value_x=sum(v['x'] for v in group)/len(group)
        run_rows=[]
        for i,anchor in enumerate(run):
            start=anchor['y']-3
            end=run[i+1]['y']-3 if i+1<len(run) else run[-1]['y']+max(20,page.rect.height*.025)
            if i==len(run)-1:
                ends=[l['y'] for l in lines if anchor['y']<l['y']<end and abs(l['x']-anchor['x'])<30 and re.fullmatch(r'\d{1,2}\s*[.)]',l['text'])]
                if ends:end=min(ends)-2
            local=[l for l in lines if start<=l['y']<end and anchor['x']-5<=l['x']<=value_x+max(150,page.rect.width*.075)]
            if not local:continue
            matches=[]
            for l in local:
                m=INCH.search(l['text'])
                if m and l['x']>=value_x-max(90,page.rect.width*.05):matches.append((l,m.group(0)))
            if not matches:continue
            first=matches[0][0]
            labels=[]
            for l in sorted(local,key=lambda x:(x['y'],x['x'])):
                if l['x']>=value_x-10 and l['x']>anchor['x']+50:continue
                s=l['text']
                m=LETTER.match(s)
                if m and l['x']<anchor['x']+max(28,page.rect.width*.015):s=m.group(2)
                if not s or INCH.search(s):continue
                labels.append(s.strip(' :;'))
            name=' '.join(labels).strip(' :;')
            if not name or not ELEMENT.search(name):continue
            run_rows.append({'file':filename,'sheet':sheet,'page':page.number+1,'letter':anchor['letter'],'element':name,'cover':matches[0][1].strip()})
        if len(run_rows)>=2:
            rows.extend(run_rows)
            if len(run_rows)<len(run):
                issues.append({'file':filename,'sheet':sheet,'page':page.number+1,'warning':f'Cover schedule has {len(run)} labeled rows but only {len(run_rows)} readable values.'})
    rows=list({(r['file'],r['sheet'],r['letter'],r['element'],r['cover']):r for r in rows}.values())
    return rows,issues
