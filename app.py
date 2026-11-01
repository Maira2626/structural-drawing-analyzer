"""Structural drawing specification and reinforcement schedule extractor.
Run: streamlit run app.py
PDF-native text is used; scanned-only pages are explicitly flagged.
"""
import io
import re
import json
import csv
import zipfile
from collections import defaultdict
from pathlib import Path

import fitz
import pandas as pd
import streamlit as st

STRENGTH_RE = re.compile(r'(?<!\d)(?:3|4|5|6|7|8|9|10|12|14)[,.]?000\s*(?:-\s*)?PSI\b', re.I)
BAR_RE = re.compile(r'^#(?:[3-9]|10|11|14)$')
HEADINGS = [("Lap splice", "LAP SPLICE LENGTH", 9), ("Straight development", "STRAIGHT BAR DEVELOPMENT LENGTH", 9), ("Hooked development", "HOOKED TENSION DEVELOPMENT", 6)]
COVER_RE = re.compile(r'(?i)\b(?:MINIMUM\s+)?(?:CLEAR\s+)?COVER\b')


def sheet_id(page):
    candidates = re.findall(r'\b(?:FO|S)-\d{3}\.\d{2}\b', page.get_text())
    return candidates[-1] if candidates else f'PDF page {page.number+1}'


def rows_from_words(page, clip):
    words = page.get_text('words', clip=clip, sort=True)
    clusters = []
    for w in sorted(words, key=lambda w: (w[1], w[0])):
        if not clusters or abs(w[1]-clusters[-1]['y']) > 3.2:
            clusters.append({'y':w[1], 'words':[w]})
        else:
            clusters[-1]['words'].append(w)
    return [(c['y'], ' '.join(w[4] for w in sorted(c['words'],key=lambda w:w[0]))) for c in clusters]


def get_words(page):
    return page.get_text('words', sort=False)


def extract_strengths(page, filename):
    # All explicit strengths are retained; proximity text gives context instead of guessing assignments.
    results=[]
    words=get_words(page)
    for w in words:
        if 'PSI' not in w[4].upper():continue
        # PSI label often separate from numerical value; use words on same line immediately to its left.
        group=sorted([z for z in words if abs(z[1]-w[1])<4 and z[0]<w[2] and z[2]>w[0]-110],key=lambda z:z[0])
        snippet=' '.join(z[4] for z in group)
        mm=list(STRENGTH_RE.finditer(snippet.replace('PSI,','PSI').replace('PSI.','PSI')))
        if not mm:continue
        psi=int(re.sub(r'[^0-9]','',mm[-1].group().upper().replace('PSI','')))
        # Prioritize note lines in the left column; schedule headers are handled in tables.
        line_words=sorted([z for z in words if abs(z[1]-w[1])<4 and z[0]<w[0]],key=lambda z:z[0])
        context=' '.join(z[4] for z in line_words[-70:])
        results.append({'file':filename,'sheet':sheet_id(page),'page':page.number+1,'strength_psi':psi,
                        'nearby_text':context[-440:],'source':'Explicit PSI notation','review':'Confirm element/floor assignment against drawing'})
    # deduplicate same page and local context, not different locations
    return list({(r['page'],r['strength_psi'],r['nearby_text']):r for r in results}.values())


def extract_cover(page, filename):
    words=get_words(page);results=[]
    # Target notes and preserve nearby text, including separately arranged numeric values.
    for w in words:
        if not COVER_RE.search(w[4]):continue
        clip=fitz.Rect(max(0,w[0]-370),max(0,w[1]-28),min(page.rect.width,w[2]+460),min(page.rect.height,w[3]+75))
        txt=' '.join(t for _,t in rows_from_words(page,clip))
        results.append({'file':filename,'sheet':sheet_id(page),'page':page.number+1,'cover_note':txt[:650],
                        'review':'Check applicability and drawing-specific exceptions'})
    return list({(r['page'],r['cover_note']):r for r in results}.values())


def find_headings(words, phrase):
    # Heading text may be grouped into a single block but is word-split here.
    matching=[]
    for w in words:
        if w[4].upper().strip(' :') not in {phrase.split()[0],phrase.split()[0]+':'}:continue
        nearby=sorted([z for z in words if abs(z[1]-w[1])<5 and w[0]-4<=z[0]<w[0]+500],key=lambda z:z[0])
        s=' '.join(z[4] for z in nearby).upper()
        if phrase in s:matching.append((w[0],w[1]))
    return matching


def extract_schedule(page, filename):
    words=get_words(page);output=[];warnings=[]
    for title,phrase,numcols in HEADINGS:
        starts=find_headings(words,phrase)
        if not starts:
            continue
        hx,hy=starts[0]
        # Locate bar labels in the schedule immediately below the heading.
        candidates=[w for w in words if BAR_RE.fullmatch(w[4]) and hx-300<w[0]<hx+100 and hy+(20 if title=='Hooked development' else 65)<w[1]<hy+320]
        if not candidates:
            warnings.append(f'{sheet_id(page)}: {title} heading found but no bar labels parsed')
            continue
        groups=defaultdict(list)
        for w in candidates:groups[round(w[0]/12)*12].append(w)
        labels=max(groups.values(),key=len)
        labels=sorted(labels,key=lambda w:w[1])
        base_x=sum(w[0] for w in labels)/len(labels)
        # For 9-column schedules, normal 6/8/10 ksi headings; hooked tables have 2 cols per strength.
        ncols=9 if title!='Hooked development' else 6
        for j,label in enumerate(labels):
            y=label[1]
            if title=='Hooked development':
                # Hook section has a 6-column layout.
                offsets=[84,165,246,325,406,487]
            else:
                offsets=[59,99,159,221,261,321,383,423,483]
            cells=[]
            for off in offsets:
                near=[w for w in words if abs(w[1]-y)<5 and abs(w[0]-(base_x+off))<13 and re.fullmatch(r'\d+',w[4])]
                cells.append(int(min(near,key=lambda w:abs(w[0]-(base_x+off)))[4]) if near else None)
            for k,psi in enumerate([6000,8000,10000]):
                vals=cells[k*(ncols//3):(k+1)*(ncols//3)]
                if title=='Hooked development':
                    typ, ext=vals
                    output.append({'file':filename,'sheet':sheet_id(page),'page':page.number+1,'schedule':title,
                          'bar_size':label[4],'concrete_psi':psi,'tension_typ_in':typ,'tension_top_in':None,
                          'compression_in':None,'hook_extension_in':ext,'review':'Verify table values and footnotes'})
                else:
                    typ,top,comp=vals
                    output.append({'file':filename,'sheet':sheet_id(page),'page':page.number+1,'schedule':title,
                          'bar_size':label[4],'concrete_psi':psi,'tension_typ_in':typ,'tension_top_in':top,
                          'compression_in':comp,'hook_extension_in':None,'review':'Verify table values and footnotes'})
        if not output: warnings.append(f'{sheet_id(page)}: Unable to obtain cells for {title}')
    return output,warnings


def extract_notes(page, filename):
    # Retain significant notes verbatim so placement rules/adjustment multipliers are not lost.
    text=page.get_text(sort=True)
    patterns=['BAR LENGTH TABLE NOTES','MECHANICAL SPLICE ONLY','REINFORCING BARS SHALL',
              'REINFORCEMENT SHALL','AIR ENTRAIN','CORROSION INHIBIT','LAP ','HOOK','COVER']
    lines=text.splitlines();out=[]
    for i,line in enumerate(lines):
        if any(t in line.upper() for t in patterns):
            out.append({'file':filename,'sheet':sheet_id(page),'page':page.number+1,'note':
                        ' '.join(lines[max(0,i-1):min(len(lines),i+4)])[:500]})
    return out


def extract_files(files):
    results={k:[] for k in ['Strengths','Covers','Rebar schedules','Relevant notes','QA warnings']}
    for f in files:
        data=f.getvalue();doc=fitz.open(stream=data,filetype='pdf')
        for page in doc:
            name=f.name
            if len(page.get_text().strip())<60:
                results['QA warnings'].append({'file':name,'sheet':sheet_id(page),'page':page.number+1,
                     'warning':'Little/no embedded text. Scanned/outlined page may require OCR or visual review.'})
                continue
            results['Strengths']+=extract_strengths(page,name)
            results['Covers']+=extract_cover(page,name)
            arr,warn=extract_schedule(page,name)
            results['Rebar schedules']+=arr
            results['Relevant notes']+=extract_notes(page,name)
            results['QA warnings'] += [{'file':name,'sheet':sheet_id(page),'page':page.number+1,'warning':x} for x in warn]
        doc.close()
    # Flag blanks explicitly, rather than fabricating values.
    for row in results['Rebar schedules']:
        fields=['tension_typ_in','hook_extension_in'] if row['schedule']=='Hooked development' else ['tension_typ_in','tension_top_in','compression_in']
        if any(row[x] is None for x in fields):
            results['QA warnings'].append({'file':row['file'],'sheet':row['sheet'],'page':row['page'],
               'warning':f"Missing table cell: {row['schedule']}, {row['bar_size']}, {row['concrete_psi']} psi"})
    return results


def excel_bytes(results):
    out=io.BytesIO()
    with pd.ExcelWriter(out,engine='openpyxl') as writer:
        for name,rows in results.items():
            pd.DataFrame(rows if rows else [{'message':'No records detected'}]).to_excel(writer,sheet_name=name[:31],index=False)
            ws=writer.sheets[name[:31]]
            ws.freeze_panes='A2';ws.auto_filter.ref=ws.dimensions
            for col in ws.columns:
                letter=col[0].column_letter
                ws.column_dimensions[letter].width=min(65,max(15,max(len(str(c.value or '')) for c in list(col)[:100])+2))
    return out.getvalue()

st.set_page_config(page_title='Structural PDF Extractor V3',layout='wide')
st.title('Structural Drawing PDF Extractor — V3')
st.caption('Extract concrete strengths, covers, lap splice/development schedules and source-linked notes. Review all engineering results against the originals.')
files=st.file_uploader('Upload foundation and structural drawing PDFs',type=['pdf'],accept_multiple_files=True)
if files and st.button('Extract drawing information',type='primary'):
    with st.spinner('Reading PDF drawing text and schedules…'):
        result=extract_files(files)
    st.session_state['extract_result']=result
if 'extract_result' in st.session_state:
    result=st.session_state['extract_result']
    for name,rows in result.items():
        with st.expander(f'{name} ({len(rows)})',expanded=name in ['Strengths','Covers','Rebar schedules']):
            st.dataframe(pd.DataFrame(rows),use_container_width=True,hide_index=True)
    st.download_button('Download Excel report',data=excel_bytes(result),file_name='structural_extraction_v3.xlsx',mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    st.download_button('Download JSON',data=json.dumps(result,indent=2),file_name='structural_extraction_v3.json',mime='application/json')
    st.warning('Numbers from drawings must be verified by an engineer. OCR for scanned-only drawings is not included. Missing cells and conflicting notes require manual review.')
