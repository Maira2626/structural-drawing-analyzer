"""Structural drawing extractor - cleaner schedule-style UI.
Run: streamlit run app.py
"""
import io
import json
import re
import zipfile
from collections import defaultdict
from pathlib import Path

import fitz
import pandas as pd
import streamlit as st

BAR_RE = re.compile(r'^#(?:[3-9]|10|11|14)$')
HEADINGS = [
    ("Lap splice", "LAP SPLICE LENGTH"),
    ("Straight development", "STRAIGHT BAR DEVELOPMENT LENGTH"),
    ("Hooked development", "HOOKED TENSION DEVELOPMENT"),
]


# -------------------------
# Extraction helpers
# -------------------------
def sheet_id(page):
    candidates = re.findall(r'\b(?:FO|S)-\d{3}\.\d{2}\b', page.get_text())
    return candidates[-1] if candidates else f'PDF page {page.number + 1}'


def get_text_blocks(page):
    return page.get_text("blocks", sort=True)


def get_words(page):
    return page.get_text("words", sort=False)


def parse_strength_block(block_text, file_name, sheet, page_num):
    lines = [ln.strip() for ln in block_text.splitlines() if ln.strip()]
    rows = []
    i = 0
    current_letter = None
    current_item = None
    while i < len(lines):
        line = lines[i]
        if re.fullmatch(r'[A-G]\.', line):
            current_letter = line[0]
            item = lines[i + 1].rstrip(':') if i + 1 < len(lines) else ''
            current_item = item
            # look ahead for either direct psi or floor-specific sub-rows
            if i + 2 < len(lines) and 'PSI' in lines[i + 2].upper():
                strength_line = lines[i + 2]
                psi = int(re.sub(r'[^0-9]', '', strength_line.split('PSI')[0]))
                weight = 'NORMAL WEIGHT' if 'NORMAL WEIGHT' in strength_line.upper() else ''
                rows.append({
                    'file': file_name,
                    'sheet': sheet,
                    'page': page_num,
                    'letter': current_letter,
                    'element': current_item,
                    'level_range': '',
                    'strength_psi': psi,
                    'weight': weight,
                })
                i += 3
                continue
            elif i + 4 < len(lines):
                # multi-line / floor-specific case
                j = i + 2
                while j + 1 < len(lines) and not re.fullmatch(r'[A-G]\.', lines[j]):
                    if 'PSI' in lines[j + 1].upper():
                        level = lines[j]
                        strength_line = lines[j + 1]
                        psi = int(re.sub(r'[^0-9]', '', strength_line.split('PSI')[0]))
                        weight = 'NORMAL WEIGHT' if 'NORMAL WEIGHT' in strength_line.upper() else ''
                        rows.append({
                            'file': file_name,
                            'sheet': sheet,
                            'page': page_num,
                            'letter': current_letter,
                            'element': current_item,
                            'level_range': level,
                            'strength_psi': psi,
                            'weight': weight,
                        })
                        j += 2
                    else:
                        break
                i = j
                continue
        i += 1
    return rows


def parse_cover_block(block_text, file_name, sheet, page_num):
    lines = [ln.strip() for ln in block_text.splitlines() if ln.strip()]
    rows = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if re.fullmatch(r'[A-G]\.', line):
            letter = line[0]
            element = lines[i + 1].rstrip(':') if i + 1 < len(lines) else ''
            cover = lines[i + 2] if i + 2 < len(lines) else ''
            rows.append({
                'file': file_name,
                'sheet': sheet,
                'page': page_num,
                'letter': letter,
                'element': element,
                'cover': cover,
            })
            i += 3
        else:
            i += 1
    return rows


def extract_strengths_and_covers(page, filename):
    strengths, covers, relevant_notes = [], [], []
    blocks = get_text_blocks(page)
    sheet = sheet_id(page)
    page_num = page.number + 1

    for _, _, _, _, text, *_ in blocks:
        upper = text.upper()
        if 'CONCRETE SHALL HAVE A MIN. COMPRESSIVE STRENGTH AT 28 DAYS' in upper:
            relevant_notes.append({
                'file': filename,
                'sheet': sheet,
                'page': page_num,
                'category': 'Strength note heading',
                'note': 'CONCRETE SHALL HAVE A MIN. COMPRESSIVE STRENGTH AT 28 DAYS AS FOLLOWS, UNLESS OTHERWISE NOTED ON THE DRAWINGS.'
            })
        elif re.search(r'^A\.$', text.strip().splitlines()[0] if text.strip().splitlines() else '') and 'SLAB ON GRADE' in upper and 'PSI' in upper:
            strengths.extend(parse_strength_block(text, filename, sheet, page_num))
        elif 'ALL CONCRETE EXPOSED TO FREEZE AND THAW' in upper:
            relevant_notes.append({
                'file': filename,
                'sheet': sheet,
                'page': page_num,
                'category': 'Concrete durability',
                'note': 'All concrete exposed to freeze and thaw in service shall have an air entraining agent.'
            })
        elif 'ALL CONCRETE EXPOSED TO MOISTURE IN SERVICE' in upper:
            relevant_notes.append({
                'file': filename,
                'sheet': sheet,
                'page': page_num,
                'category': 'Concrete durability',
                'note': 'All concrete exposed to moisture in service shall have a corrosion inhibiting admixture added.'
            })
        elif 'MINIMUM CONCRETE COVER SHALL BE AS FOLLOWS' in upper:
            covers.extend(parse_cover_block(text, filename, sheet, page_num))
        elif 'ALL REINFORCING BARS SHALL CONFORM TO ASTM A615' in upper or 'ALL REINFORCING BARS SHALL CONFORM TO ASTM A615.' in upper:
            relevant_notes.append({
                'file': filename,
                'sheet': sheet,
                'page': page_num,
                'category': 'Rebar material',
                'note': 'All reinforcing bars shall conform to ASTM A615. Grade 60 steel for #8 bars and lower. Grade 80 steel for #9 bars and higher.'
            })

    # Deduplicate within sheet
    strengths = list({(r['sheet'], r['letter'], r['element'], r['level_range'], r['strength_psi']): r for r in strengths}.values())
    covers = list({(r['sheet'], r['letter'], r['element'], r['cover']): r for r in covers}.values())
    relevant_notes = list({(r['sheet'], r['category'], r['note']): r for r in relevant_notes}.values())
    return strengths, covers, relevant_notes


def find_headings(words, phrase):
    matching = []
    first_word = phrase.split()[0]
    for w in words:
        if w[4].upper().strip(' :') not in {first_word, first_word + ':'}:
            continue
        nearby = sorted([z for z in words if abs(z[1] - w[1]) < 5 and w[0] - 4 <= z[0] < w[0] + 540], key=lambda z: z[0])
        s = ' '.join(z[4] for z in nearby).upper()
        if phrase in s:
            matching.append((w[0], w[1]))
    return matching


def extract_schedule(page, filename):
    words = get_words(page)
    output = []
    warnings = []
    sheet = sheet_id(page)
    page_num = page.number + 1

    for title, phrase in HEADINGS:
        starts = find_headings(words, phrase)
        if not starts:
            continue
        hx, hy = starts[0]
        candidates = [
            w for w in words
            if BAR_RE.fullmatch(w[4])
            and hx - 300 < w[0] < hx + 100
            and hy + (20 if title == 'Hooked development' else 65) < w[1] < hy + 320
        ]
        if not candidates:
            warnings.append({'file': filename, 'sheet': sheet, 'page': page_num, 'warning': f'{title}: heading found but no bar labels parsed'})
            continue

        groups = defaultdict(list)
        for w in candidates:
            groups[round(w[0] / 12) * 12].append(w)
        labels = sorted(max(groups.values(), key=len), key=lambda w: w[1])
        base_x = sum(w[0] for w in labels) / len(labels)

        if title == 'Hooked development':
            offsets = [84, 165, 246, 325, 406, 487]
        else:
            offsets = [59, 99, 159, 221, 261, 321, 383, 423, 483]

        for label in labels:
            y = label[1]
            cells = []
            for off in offsets:
                near = [
                    w for w in words
                    if abs(w[1] - y) < 5 and abs(w[0] - (base_x + off)) < 13 and re.fullmatch(r'\d+', w[4])
                ]
                cells.append(int(min(near, key=lambda w: abs(w[0] - (base_x + off)))[4]) if near else None)

            for k, psi in enumerate([6000, 8000, 10000]):
                if title == 'Hooked development':
                    vals = cells[k * 2:(k + 1) * 2]
                    typ, ext = vals
                    output.append({
                        'file': filename, 'sheet': sheet, 'page': page_num, 'schedule': title,
                        'bar_size': label[4], 'concrete_psi': psi,
                        'tension_typ_in': typ, 'tension_top_in': None, 'compression_in': None,
                        'hook_extension_in': ext,
                    })
                else:
                    vals = cells[k * 3:(k + 1) * 3]
                    typ, top, comp = vals
                    output.append({
                        'file': filename, 'sheet': sheet, 'page': page_num, 'schedule': title,
                        'bar_size': label[4], 'concrete_psi': psi,
                        'tension_typ_in': typ, 'tension_top_in': top, 'compression_in': comp,
                        'hook_extension_in': None,
                    })
    for row in output:
        req = ['tension_typ_in', 'hook_extension_in'] if row['schedule'] == 'Hooked development' else ['tension_typ_in', 'tension_top_in', 'compression_in']
        if any(row[k] is None for k in req):
            warnings.append({'file': row['file'], 'sheet': row['sheet'], 'page': row['page'], 'warning': f"Missing cell in {row['schedule']} - {row['bar_size']} @ {row['concrete_psi']} psi"})
    return output, warnings


def extract_files(files):
    result = {
        'strengths': [],
        'covers': [],
        'rebar_schedules': [],
        'notes': [],
        'qa_warnings': [],
    }
    for file in files:
        doc = fitz.open(stream=file.getvalue(), filetype='pdf')
        for page in doc:
            name = file.name
            if len(page.get_text().strip()) < 60:
                result['qa_warnings'].append({
                    'file': name,
                    'sheet': sheet_id(page),
                    'page': page.number + 1,
                    'warning': 'Little or no embedded text detected. This page may need OCR/manual review.'
                })
                continue
            strengths, covers, notes = extract_strengths_and_covers(page, name)
            schedules, warnings = extract_schedule(page, name)
            result['strengths'].extend(strengths)
            result['covers'].extend(covers)
            result['notes'].extend(notes)
            result['rebar_schedules'].extend(schedules)
            result['qa_warnings'].extend(warnings)
        doc.close()

    # final dedupe across files/sheets/records
    result['strengths'] = list({(r['file'], r['sheet'], r['letter'], r['element'], r['level_range'], r['strength_psi']): r for r in result['strengths']}.values())
    result['covers'] = list({(r['file'], r['sheet'], r['letter'], r['element'], r['cover']): r for r in result['covers']}.values())
    result['rebar_schedules'] = list({(r['file'], r['sheet'], r['schedule'], r['bar_size'], r['concrete_psi']): r for r in result['rebar_schedules']}.values())
    result['notes'] = list({(r['file'], r['sheet'], r['category'], r['note']): r for r in result['notes']}.values())
    result['qa_warnings'] = list({(r['file'], r['sheet'], r['warning']): r for r in result['qa_warnings']}.values())
    return result


# -------------------------
# Display helpers
# -------------------------
def inject_css():
    st.markdown(
        """
        <style>
        .main {background: #f7f8fc;}
        .block-container {padding-top: 1.5rem; padding-bottom: 2rem; max-width: 1400px;}
        h1, h2, h3 {color: #1d2433;}
        .hero {
            background: linear-gradient(135deg, #ffffff 0%, #eef3ff 100%);
            border: 1px solid #dde5f5;
            padding: 1.2rem 1.4rem;
            border-radius: 18px;
            margin-bottom: 1rem;
            box-shadow: 0 8px 24px rgba(20, 33, 61, 0.06);
        }
        .subtle {color: #55627a; font-size: 0.98rem;}
        .card {
            background: white;
            border: 1px solid #e5e9f2;
            border-radius: 16px;
            padding: 0.8rem 1rem;
            box-shadow: 0 6px 18px rgba(20, 33, 61, 0.05);
            margin-bottom: 1rem;
        }
        .metric-card {
            background: white;
            border: 1px solid #e5e9f2;
            border-radius: 16px;
            padding: 0.8rem 1rem;
            text-align: center;
            box-shadow: 0 6px 18px rgba(20, 33, 61, 0.05);
        }
        .metric-value {font-size: 1.7rem; font-weight: 700; color: #1d2433;}
        .metric-label {font-size: 0.9rem; color: #66738a;}
        .sheet-badge {
            display: inline-block;
            background: #eef3ff;
            border: 1px solid #d8e3ff;
            color: #3556a8;
            border-radius: 999px;
            font-size: 0.82rem;
            padding: 0.25rem 0.65rem;
            margin-right: 0.4rem;
            margin-bottom: 0.45rem;
        }
        table.pretty {
            width: 100%; border-collapse: collapse; background: white; font-size: 13px;
            border: 1px solid #dde5f5; overflow: hidden; border-radius: 12px;
        }
        table.pretty th, table.pretty td {
            border: 1px solid #e2e8f5; padding: 8px 10px; text-align: center;
        }
        table.pretty th {
            background: #edf2ff; color: #1d2433; font-weight: 700;
        }
        table.pretty td.left, table.pretty th.left {text-align: left;}
        .small-note {font-size: 0.86rem; color: #5e6c84;}
        .note-item {
            background: #ffffff; border-left: 4px solid #6b8afd; border-radius: 10px;
            padding: 0.75rem 0.9rem; margin-bottom: 0.55rem; border-top:1px solid #e7ebf5; border-right:1px solid #e7ebf5; border-bottom:1px solid #e7ebf5;
        }
        .warning-item {
            background: #fffaf0; border-left: 4px solid #f59e0b; border-radius: 10px;
            padding: 0.75rem 0.9rem; margin-bottom: 0.55rem; border-top:1px solid #f5e7be; border-right:1px solid #f5e7be; border-bottom:1px solid #f5e7be;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_metric(label, value):
    st.markdown(f"<div class='metric-card'><div class='metric-value'>{value}</div><div class='metric-label'>{label}</div></div>", unsafe_allow_html=True)


def render_strength_table(rows):
    rows = sorted(rows, key=lambda r: (r['letter'], r['element'], r['level_range']))
    html = ["<table class='pretty'>"]
    html.append("<tr><th style='width:8%'>Ref</th><th class='left' style='width:42%'>Element</th><th class='left' style='width:28%'>Level / Range</th><th style='width:12%'>f&#8242;c (psi)</th><th style='width:10%'>Weight</th></tr>")
    for r in rows:
        html.append(
            f"<tr><td>{r['letter']}</td><td class='left'>{r['element']}</td><td class='left'>{r['level_range'] or '—'}</td><td>{r['strength_psi']:,}</td><td>{r['weight'] or '—'}</td></tr>"
        )
    html.append("</table>")
    st.markdown(''.join(html), unsafe_allow_html=True)


def render_cover_table(rows):
    rows = sorted(rows, key=lambda r: (r['letter'], r['element']))
    html = ["<table class='pretty'>"]
    html.append("<tr><th style='width:10%'>Ref</th><th class='left' style='width:60%'>Element</th><th style='width:30%'>Minimum Cover</th></tr>")
    for r in rows:
        html.append(f"<tr><td>{r['letter']}</td><td class='left'>{r['element']}</td><td>{r['cover']}</td></tr>")
    html.append("</table>")
    st.markdown(''.join(html), unsafe_allow_html=True)


def render_schedule_table(rows, schedule_name):
    rows = sorted(rows, key=lambda r: (int(re.sub(r'[^0-9]', '', r['bar_size'])), r['concrete_psi']))
    bar_sizes = []
    for r in rows:
        if r['bar_size'] not in bar_sizes:
            bar_sizes.append(r['bar_size'])

    # index by bar, psi
    idx = {(r['bar_size'], r['concrete_psi']): r for r in rows}
    html = ["<table class='pretty'>"]
    if schedule_name == 'Hooked development':
        html.append("<tr><th rowspan='2'>Bar size</th><th colspan='2'>6,000 psi</th><th colspan='2'>8,000 psi</th><th colspan='2'>10,000 psi</th></tr>")
        html.append("<tr><th>Ldh</th><th>Ext.</th><th>Ldh</th><th>Ext.</th><th>Ldh</th><th>Ext.</th></tr>")
        for b in bar_sizes:
            html.append(f"<tr><td>{b}</td>")
            for psi in [6000, 8000, 10000]:
                r = idx.get((b, psi), {})
                html.append(f"<td>{r.get('tension_typ_in', '—') if r.get('tension_typ_in') is not None else '—'}</td>")
                html.append(f"<td>{r.get('hook_extension_in', '—') if r.get('hook_extension_in') is not None else '—'}</td>")
            html.append("</tr>")
    else:
        html.append("<tr><th rowspan='2'>Bar size</th><th colspan='3'>6,000 psi</th><th colspan='3'>8,000 psi</th><th colspan='3'>10,000 psi</th></tr>")
        html.append("<tr><th>Typ.</th><th>Top</th><th>Comp.</th><th>Typ.</th><th>Top</th><th>Comp.</th><th>Typ.</th><th>Top</th><th>Comp.</th></tr>")
        for b in bar_sizes:
            html.append(f"<tr><td>{b}</td>")
            for psi in [6000, 8000, 10000]:
                r = idx.get((b, psi), {})
                html.append(f"<td>{r.get('tension_typ_in', '—') if r.get('tension_typ_in') is not None else '—'}</td>")
                html.append(f"<td>{r.get('tension_top_in', '—') if r.get('tension_top_in') is not None else '—'}</td>")
                html.append(f"<td>{r.get('compression_in', '—') if r.get('compression_in') is not None else '—'}</td>")
            html.append("</tr>")
    html.append("</table>")
    st.markdown(''.join(html), unsafe_allow_html=True)


def group_by_sheet(rows):
    grouped = defaultdict(list)
    for row in rows:
        key = (row['file'], row['sheet'], row['page'])
        grouped[key].append(row)
    return grouped


def excel_bytes(result):
    out = io.BytesIO()
    with pd.ExcelWriter(out, engine='openpyxl') as writer:
        pd.DataFrame(result['strengths']).to_excel(writer, sheet_name='Strengths', index=False)
        pd.DataFrame(result['covers']).to_excel(writer, sheet_name='Covers', index=False)
        pd.DataFrame(result['rebar_schedules']).to_excel(writer, sheet_name='RebarSchedules', index=False)
        pd.DataFrame(result['notes']).to_excel(writer, sheet_name='Notes', index=False)
        pd.DataFrame(result['qa_warnings']).to_excel(writer, sheet_name='QAWarnings', index=False)
        for ws in writer.book.worksheets:
            ws.freeze_panes = 'A2'
            ws.auto_filter.ref = ws.dimensions
            for col in ws.columns:
                letter = col[0].column_letter
                max_len = max(len(str(c.value or '')) for c in col[:100])
                ws.column_dimensions[letter].width = min(40, max(12, max_len + 2))
    return out.getvalue()


def zip_bytes(result):
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('structural_extraction_v4.json', json.dumps(result, indent=2))
        zf.writestr('strengths.csv', pd.DataFrame(result['strengths']).to_csv(index=False))
        zf.writestr('covers.csv', pd.DataFrame(result['covers']).to_csv(index=False))
        zf.writestr('rebar_schedules.csv', pd.DataFrame(result['rebar_schedules']).to_csv(index=False))
        zf.writestr('notes.csv', pd.DataFrame(result['notes']).to_csv(index=False))
        zf.writestr('qa_warnings.csv', pd.DataFrame(result['qa_warnings']).to_csv(index=False))
    return data.getvalue()


# -------------------------
# App UI
# -------------------------
def run_app():
    inject_css()
    st.set_page_config(page_title='Structural Drawing PDF Extractor — V4', layout='wide')
    st.markdown("<div class='hero'><h1>Structural Drawing PDF Extractor — V4</h1><div class='subtle'>Compact drawing-style schedules for concrete strengths, covers, lap splice lengths, development lengths, and key notes — with a cleaner layout designed for quick review.</div></div>", unsafe_allow_html=True)

    files = st.file_uploader('Upload foundation and structural drawing PDFs', type=['pdf'], accept_multiple_files=True)

    col1, col2 = st.columns([1, 3])
    with col1:
        run = st.button('Extract drawing information', type='primary', use_container_width=True)
    with col2:
        st.markdown("<div class='small-note'>Tip: upload both foundation and superstructure sets. Results are grouped by drawing sheet so you can review them with minimal scrolling.</div>", unsafe_allow_html=True)

    if files and run:
        with st.spinner('Reading drawings and building compact schedules…'):
            st.session_state['extract_result_v4'] = extract_files(files)

    if 'extract_result_v4' in st.session_state:
        result = st.session_state['extract_result_v4']

        c1, c2, c3, c4, c5 = st.columns(5)
        with c1:
            render_metric('Files', len({r['file'] for k in ['strengths', 'covers', 'rebar_schedules', 'notes'] for r in result[k]}))
        with c2:
            render_metric('Strength rows', len(result['strengths']))
        with c3:
            render_metric('Cover rows', len(result['covers']))
        with c4:
            render_metric('Schedule entries', len(result['rebar_schedules']))
        with c5:
            render_metric('QA warnings', len(result['qa_warnings']))

        tabs = st.tabs(['Overview', 'Strengths', 'Covers', 'Rebar schedules', 'Notes & warnings', 'Downloads'])

        with tabs[0]:
            st.markdown("<div class='card'><h3>Detected drawing sheets</h3>", unsafe_allow_html=True)
            seen = []
            for group_name in ['strengths', 'covers', 'rebar_schedules']:
                for r in result[group_name]:
                    badge = (r['file'], r['sheet'], r['page'])
                    if badge not in seen:
                        seen.append(badge)
            if seen:
                badges = ''.join([f"<span class='sheet-badge'>{Path(f).name} · {s} · p.{p}</span>" for f, s, p in seen])
                st.markdown(badges, unsafe_allow_html=True)
            else:
                st.info('No schedule-style data detected yet.')
            st.markdown("</div>", unsafe_allow_html=True)
            st.markdown("<div class='card'><h3>What changed in V4</h3><ul><li>Much shorter output with schedule-style tables.</li><li>Strengths and covers shown like drawing notes, not long raw lists.</li><li>Lap/development lengths grouped into compact tables.</li><li>Cleaner layout for easier review on one screen.</li></ul></div>", unsafe_allow_html=True)

        with tabs[1]:
            groups = group_by_sheet(result['strengths'])
            if not groups:
                st.info('No concrete strength schedules detected.')
            for (file_name, sheet, page_num), rows in groups.items():
                st.markdown(f"<div class='card'><h3>Concrete Strength Schedule</h3><div class='small-note'>{Path(file_name).name} · {sheet} · page {page_num}</div>", unsafe_allow_html=True)
                render_strength_table(rows)
                st.markdown("</div>", unsafe_allow_html=True)

        with tabs[2]:
            groups = group_by_sheet(result['covers'])
            if not groups:
                st.info('No concrete cover schedules detected.')
            for (file_name, sheet, page_num), rows in groups.items():
                st.markdown(f"<div class='card'><h3>Concrete Cover Schedule</h3><div class='small-note'>{Path(file_name).name} · {sheet} · page {page_num}</div>", unsafe_allow_html=True)
                render_cover_table(rows)
                st.markdown("</div>", unsafe_allow_html=True)

        with tabs[3]:
            schedule_groups = defaultdict(list)
            for r in result['rebar_schedules']:
                schedule_groups[(r['file'], r['sheet'], r['page'], r['schedule'])].append(r)
            if not schedule_groups:
                st.info('No rebar schedule tables detected.')
            order = ['Lap splice', 'Straight development', 'Hooked development']
            for key in sorted(schedule_groups.keys(), key=lambda x: (x[0], x[1], order.index(x[3]) if x[3] in order else 99)):
                file_name, sheet, page_num, schedule_name = key
                rows = schedule_groups[key]
                st.markdown(f"<div class='card'><h3>{schedule_name} Schedule</h3><div class='small-note'>{Path(file_name).name} · {sheet} · page {page_num}</div>", unsafe_allow_html=True)
                render_schedule_table(rows, schedule_name)
                st.markdown("</div>", unsafe_allow_html=True)

        with tabs[4]:
            st.markdown("<div class='card'><h3>Key notes</h3>", unsafe_allow_html=True)
            if result['notes']:
                for n in sorted(result['notes'], key=lambda r: (r['file'], r['sheet'], r['category'])):
                    st.markdown(f"<div class='note-item'><strong>{n['category']}</strong><br><span class='small-note'>{Path(n['file']).name} · {n['sheet']} · page {n['page']}</span><br>{n['note']}</div>", unsafe_allow_html=True)
            else:
                st.info('No notes detected.')
            st.markdown("</div>", unsafe_allow_html=True)

            st.markdown("<div class='card'><h3>QA warnings</h3>", unsafe_allow_html=True)
            if result['qa_warnings']:
                for w in sorted(result['qa_warnings'], key=lambda r: (r['file'], r['sheet'])):
                    st.markdown(f"<div class='warning-item'><strong>{Path(w['file']).name} · {w['sheet']} · page {w['page']}</strong><br>{w['warning']}</div>", unsafe_allow_html=True)
            else:
                st.success('No QA warnings were generated.')
            st.markdown("</div>", unsafe_allow_html=True)

        with tabs[5]:
            st.markdown("<div class='card'><h3>Download results</h3><div class='small-note'>Exports still include the structured schedule data for Excel/JSON review.</div></div>", unsafe_allow_html=True)
            st.download_button('Download Excel report', data=excel_bytes(result), file_name='structural_extraction_v4.xlsx', mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
            st.download_button('Download ZIP bundle (JSON + CSV)', data=zip_bytes(result), file_name='structural_extraction_v4_bundle.zip', mime='application/zip')

        st.warning('Engineering review is still required. Missing cells, scanned pages, and drawing-specific exceptions must be checked against the original sheets.')


if __name__ == "__main__":
    run_app()
