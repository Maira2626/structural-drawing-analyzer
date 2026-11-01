"""Structural Drawing Analyzer V5 — compact, source-aware engineering schedules.

Run with `streamlit run app.py`. Uses embedded PDF text (no speculative OCR).
"""

import io
import json
import re
from collections import defaultdict
from html import escape
from pathlib import Path

import fitz
import pandas as pd
import streamlit as st
from extractor import extract_strength_schedules
from adaptive import extract_adaptive_strengths, extract_adaptive_covers
from rebar import extract_rebar_dynamic

REBAR_HEADINGS = (
    ("Lap splice", "LAP SPLICE LENGTH"),
    ("Straight development", "STRAIGHT BAR DEVELOPMENT LENGTH"),
    ("Hooked development", "HOOKED TENSION DEVELOPMENT"),
)
BAR_RE = re.compile(r"#(?:[3-9]|10|11)\b")
PSI_RE = re.compile(r"(?<!\d)(\d{1,2}[, ]?\d{3})\s*-?\s*PSI\b", re.I)
LETTERS = "ABCDEFG"


def sheet_id(page):
    matches = re.findall(r"\b(?:FO|FDN|F|FOUND|S|ST|STR|A|G)-\d{2,4}(?:[.\-]\d{1,3})?\b", page.get_text(), re.I)
    return matches[-1] if matches else f"PDF page {page.number + 1}"


def discipline(sheet):
    prefix = sheet.upper().split("-")[0]
    if prefix in {"FO", "F", "FDN", "FOUND"}:
        return "FO"
    if prefix in {"S", "ST", "STR"}:
        return "S"
    return "Other"


def block_lines(text):
    return [s.strip() for s in text.splitlines() if s.strip()]


def parse_strength_block(text, file, sheet, page):
    """Parse A-G concrete specifications; keep C and D floor-dependent rows.

    Handles native PDF blocks where the letter, element, level and PSI can
    each occupy a separate line. Avoids grabbing PSI table column headers.
    """
    chunks = re.split(r"(?m)^\s*([A-G])\.\s*", text)
    out = []
    for i in range(1, len(chunks), 2):
        letter, body = chunks[i], chunks[i + 1]
        lines = block_lines(body)
        if not lines:
            continue
        element = lines[0].rstrip(": ")
        if not element or PSI_RE.search(element):
            continue
        for j, line in enumerate(lines[1:], start=1):
            match = PSI_RE.search(line)
            if not match:
                continue
            level = ""
            if j > 1:
                preceding = lines[j - 1].strip()
                if not PSI_RE.search(preceding) and preceding.upper() != element.upper():
                    # The level is normally just before its corresponding PSI.
                    level = preceding
            out.append({
                "file": file, "sheet": sheet, "page": page,
                "letter": letter, "element": element,
                "level_range": level, "strength_psi": int(re.sub(r"\D", "", match.group(1))),
                "weight": "NORMAL WEIGHT" if "NORMAL WEIGHT" in line.upper() else "",
            })
    return out


def parse_cover_block(text, file, sheet, page):
    chunks = re.split(r"(?m)^\s*([A-G])\.\s*", text)
    out = []
    for i in range(1, len(chunks), 2):
        letter, body = chunks[i], chunks[i + 1]
        lines = block_lines(body)
        if len(lines) < 2:
            continue
        element = lines[0].rstrip(": ")
        cover = next((x for x in lines[1:] if re.search(r"(?:\d(?:-\d+)?/\d+|\d+)\s+INCH", x, re.I)), None)
        if cover is None:
            continue
        out.append({"file": file, "sheet": sheet, "page": page,
                    "letter": letter, "element": element, "cover": cover})
    return out


def get_strength_cover_specs(page, file):
    sheet = sheet_id(page)
    n = page.number + 1
    blocks = page.get_text("blocks")
    strengths, covers, misc = [], [], []

    for b in blocks:
        text = b[4]
        upper = text.upper()
        if "SLAB ON GRADE" in upper and "FOUNDATION WALLS" in upper and "PSI" in upper and b[0] < page.rect.width * 0.55:
            strengths.extend(parse_strength_block(text, file, sheet, n))
        elif "MINIMUM CONCRETE COVER SHALL BE AS FOLLOWS" in upper:
            covers.extend(parse_cover_block(text, file, sheet, n))
        # Capture explicit compressive-strength notes outside the A-G schedule.
        elif ("COMPRESSIVE STRENGTH" in upper and "PSI" in upper
              and not ("SLAB ON GRADE" in upper and "FOUNDATION WALLS" in upper)):
            found = PSI_RE.findall(text)
            if found:
                misc.append({"file": file, "sheet": sheet, "page": n,
                             "strengths_psi": ", ".join(str(int(x.replace(",", "").replace(" ", ""))) for x in found),
                             "note": " ".join(block_lines(text))[:650]})
    return strengths, covers, misc


def extract_rebar(page, file):
    """Use actual schedule header blocks to avoid falsely reading CMU splice notes."""
    sheet, page_n = sheet_id(page), page.number + 1
    words, blocks = page.get_text("words", sort=False), page.get_text("blocks", sort=False)
    data, issues = [], []
    for name, phrase in REBAR_HEADINGS:
        headers = [b for b in blocks if b[4].strip().upper().startswith(phrase)]
        if not headers:
            continue
        header = min(headers, key=lambda b: b[1])
        hx, hy = header[:2]
        labels = [w for w in words if BAR_RE.fullmatch(w[4])
                  and hx - 300 < w[0] < hx + 100
                  and hy + (20 if name == "Hooked development" else 65) < w[1] < hy + 320]
        if not labels:
            issues.append({"file": file, "sheet": sheet, "page": page_n,
                           "warning": f"{name}: schedule heading was found but rows could not be read."})
            continue
        grouped = defaultdict(list)
        for w in labels:
            grouped[round(w[0] / 12) * 12].append(w)
        labels = sorted(max(grouped.values(), key=len), key=lambda w: w[1])
        if len(labels) < 3:
            issues.append({"file": file, "sheet": sheet, "page": page_n,
                           "warning": f"{name}: too few recognizable bar-size rows."})
            continue
        x_base = sum(w[0] for w in labels) / len(labels)
        offsets = ([84, 165, 246, 325, 406, 487] if name == "Hooked development"
                   else [59, 99, 159, 221, 261, 321, 383, 423, 483])
        for w in labels:
            cells = []
            for offset in offsets:
                found = [p for p in words if abs(p[1] - w[1]) < 5
                         and abs(p[0] - (x_base + offset)) < 13
                         and re.fullmatch(r"\d+", p[4])]
                cells.append(int(min(found, key=lambda a: abs(a[0] - (x_base + offset)))[4]) if found else None)
            for group_i, psi in enumerate((6000, 8000, 10000)):
                if name == "Hooked development":
                    typ, ext = cells[group_i * 2:(group_i + 1) * 2]
                    top = comp = None
                else:
                    typ, top, comp = cells[group_i * 3:(group_i + 1) * 3]
                    ext = None
                record = {"file": file, "sheet": sheet, "page": page_n,
                          "schedule": name, "bar_size": w[4], "concrete_psi": psi,
                          "tension_typ_in": typ, "tension_top_in": top,
                          "compression_in": comp, "hook_extension_in": ext}
                data.append(record)
                needed = (typ, ext) if name == "Hooked development" else (typ, top, comp)
                if any(x is None for x in needed):
                    issues.append({"file": file, "sheet": sheet, "page": page_n,
                                   "warning": f"Missing {name} table cell: {w[4]} at {psi:,} psi"})
    return data, issues


def extract_files(files):
    out = {key: [] for key in ("strengths", "covers", "rebar_schedules", "other_strength_notes", "qa_warnings")}
    out["file_count"] = len(files)
    out["page_count"] = 0
    for f in files:
        doc = fitz.open(stream=f.getvalue(), filetype="pdf")
        out["page_count"] += len(doc)
        for page in doc:
            file = f.name
            if len(page.get_text().strip()) < 50:
                out["qa_warnings"].append({"file": file, "sheet": sheet_id(page), "page": page.number + 1,
                                           "warning": "Little or no embedded text; visual review/OCR may be necessary."})
                continue
            _, legacy_covers, notes = get_strength_cover_specs(page, file)
            strengths, strength_issues = extract_adaptive_strengths(page, file, sheet_id(page))
            older_strengths, older_strength_warnings = extract_strength_schedules(page, file, sheet_id(page))
            if len(older_strengths) > len(strengths):
                strengths = older_strengths
                strength_issues.append({"file": file, "sheet": sheet_id(page), "page": page.number+1,
                   "warning": "Fallback parsing recovered additional strength rows; verify their descriptions against the original drawing."})
            covers, cover_issues = extract_adaptive_covers(page, file, sheet_id(page))
            if len(legacy_covers) > len(covers):
                covers = legacy_covers
                cover_issues.append({"file": file, "sheet": sheet_id(page), "page": page.number+1,
                   "warning": "Fallback cover parsing recovered additional cover rows; verify original."})
            page_text = page.get_text()
            if ("COMPRESSIVE STRENGTH" in page_text.upper() and "PSI" in page_text.upper() and not strengths):
                strength_issues.append({"file": file, "sheet": sheet_id(page), "page": page.number+1,
                   "warning": "Compressive-strength text detected, but no structured schedule was extracted. Inspect this sheet directly."})
            rebars, issues = extract_rebar_dynamic(page, file, sheet_id(page))
            issues.extend(cover_issues)
            issues.extend(strength_issues)
            out["strengths"].extend(strengths)
            out["covers"].extend(covers)
            out["other_strength_notes"].extend(notes)
            out["rebar_schedules"].extend(rebars)
            out["qa_warnings"].extend(issues)
        doc.close()
    keys = {
        "strengths": ("file", "sheet", "letter", "element", "level_range", "strength_psi"),
        "covers": ("file", "sheet", "letter", "element", "cover"),
        "other_strength_notes": ("file", "sheet", "note"),
        "rebar_schedules": ("file", "sheet", "schedule", "bar_size", "concrete_psi"),
        "qa_warnings": ("file", "sheet", "warning"),
    }
    for section, fields in keys.items():
        out[section] = list({tuple(row[k] for k in fields): row for row in out[section]}.values())
    # Do not require a fixed A-G/A-I row count. Compare other explicit strength notes
    # only when they mention the same broad structural element and a different value.
    terms=('FOOTING','COLUMN','SLAB','WALL','BEAM','MAT','PIER','GRADE BEAM','PILE')
    for note in out['other_strength_notes']:
        upper=note['note'].upper()
        given={int(x) for x in note['strengths_psi'].split(', ') if x.isdigit()}
        for row in out['strengths']:
            if row['file']!=note['file']:continue
            shared=[term for term in terms if term in upper and term in row['element'].upper()]
            if shared and given and row['strength_psi'] not in given:
                out['qa_warnings'].append({'file':note['file'],'sheet':note['sheet'],
                    'page':note['page'],'warning':f"Potential different {shared[0].lower()} strength notes: {note['sheet']} lists {', '.join(f'{v:,}' for v in sorted(given))} psi; {row['sheet']} lists {row['strength_psi']:,} psi. Review applicability and overrides."})
                break
    return out


# ---------------------------- Presentation ----------------------------
CSS = """
<style>
:root {--navy:#13233e; --blue:#3266aa; --muted:#63718b; --line:#e1e8f2;}
.block-container {max-width: 1260px; padding-top: 1.15rem; padding-bottom: 1.3rem;}
html, body, [class*="css"] {font-family: 'Segoe UI', Arial, sans-serif;}
h1, h2, h3 {letter-spacing:-.02em; color:#182944;}
.app-hero {background:linear-gradient(118deg,#142845 0%,#24456c 75%,#315b88 100%); color:#fff;
border-radius:16px; padding:22px 28px; margin-bottom:16px; box-shadow:0 12px 28px rgba(18,35,61,.12);}
.app-kicker {color:#bdd6fc; letter-spacing:.14em; text-transform:uppercase; font-size:.71rem; font-weight:750;}
.app-title {color:white!important; font-size:1.62rem; margin:5px 0 3px 0; font-weight:750;}
.app-subtitle {color:#deebff; font-size:.89rem;}
.section-heading {display:flex; justify-content:space-between; align-items:center; margin:6px 0 10px;}
.section-heading strong {font-size:1.02rem; color:var(--navy);}
.source-line {color:#65768f; font-size:.79rem; margin:4px 0 12px;}
.source-tag {border:1px solid #d9e5f5; background:#f1f5fb; padding:4px 9px; border-radius:7px; margin-right:7px; display:inline-block; font-size:.77rem; color:#36577d; margin-bottom:4px;}
.schedule-table {border-collapse:separate; border-spacing:0; width:100%; font-size:.825rem; line-height:1.25; border:1px solid #d9e2ef; border-radius:10px; overflow:hidden; background:#fff;}
.schedule-table th {background:#eaf1fc; color:#18304f; font-weight:750;}
.schedule-table td,.schedule-table th {padding:9px 11px; border-bottom:1px solid #e6edf6; border-right:1px solid #e6edf6; vertical-align:middle; text-align:center;}
.schedule-table tr:last-child td {border-bottom:0;}
.schedule-table td:last-child,.schedule-table th:last-child {border-right:0;}
.schedule-table td.left,.schedule-table th.left {text-align:left;}
.schedule-table tbody tr:nth-child(even) {background:#f9fbff;}
.schedule-table td.number {font-weight:700; color:#193b64; font-variant-numeric:tabular-nums;}
.schedule-table td.sublevel {padding-left:22px; color:#475c75;}
.cover-foot {margin-top:9px; color:#718095; font-size:.77rem;}
.app-footer {color:#62718b; text-align:center; font-size:.79rem; border-top:1px solid #e3e9f2; margin-top:23px; padding-top:15px;}
[data-testid="stFileUploader"] {border-radius:12px;}
[data-testid="stAlert"] {border-radius:9px;}
.stButton>button[kind="primary"] {background:#2863a4; border-color:#2863a4; border-radius:8px; font-weight:700;}
.stTabs [data-baseweb="tab-list"] {gap:18px; border-bottom:1px solid #e3e9f1;}
.stTabs [data-baseweb="tab"] {padding:10px 3px; font-weight:600;}
.small-status {color:#5f718c; font-size:.82rem;}
</style>
"""


def h(value):
    return escape(str(value))


def join_values(values, fmt="number"):
    vals = sorted(set(values))
    if not vals:
        return "—"
    if fmt == "number":
        return " / ".join(f"{x:,}" for x in vals)
    return " / ".join(h(x) for x in vals)


def canonical_label(value):
    """Same label with different punctuation/case should align between FO and S."""
    t = re.sub(r"[^A-Z0-9]+", " ", str(value).upper()).strip()
    return re.sub(r"\s+", " ", t)


def comparable_rows(records, kind):
    """Compare by element and floor, never by A/B/C reference letter."""
    mapping = {}
    for r in sorted(records, key=lambda a: (discipline(a["sheet"]) != "FO", a["file"], a["page"], a["letter"])):
        key = (canonical_label(r["element"]), canonical_label(r.get("level_range", "")))
        if key not in mapping:
            mapping[key] = {"letter": r["letter"], "element": r["element"],
                            "level_range": r.get("level_range", ""), "FO": set(), "S": set(),
                            "sources": set(), "weights": set()}
        cat = discipline(r["sheet"])
        mapping[key].setdefault(cat, set()).add(r["strength_psi"] if kind == "strengths" else r["cover"])
        mapping[key]['sources'].add((r['sheet'],r['page']))
        if kind == 'strengths' and r.get('weight'):
            mapping[key]['weights'].add(r['weight'])
    return list(mapping.values())


def source_labels(records):
    output = []
    for grp in ("FO", "S", "Other"):
        seen = sorted({(r["sheet"], r["page"]) for r in records if discipline(r["sheet"]) == grp})
        if seen:
            output.append(f"<span class='source-tag'>{grp}: " + ", ".join(f"{h(sheet)} (p.{p})" for sheet, p in seen) + "</span>")
    return "".join(output)


def strength_table(records):
    rows = comparable_rows(records, "strengths")
    if not rows:
        st.info("No readable concrete-strength schedule was detected. Check the Review tab and the original PDFs.")
        return
    st.markdown("<div class='section-heading'><strong>28-Day Concrete Compressive Strength</strong><span class='small-status'>As specified in the drawing · psi</span></div>", unsafe_allow_html=True)
    st.markdown(source_labels(records), unsafe_allow_html=True)
    unknown = any(r.get("Other") for r in rows)
    other_head = "<th>Other drawings</th>" if unknown else ""
    cells = ["<table class='schedule-table'><thead><tr><th style='width:5%'>Ref.</th><th class='left' style='width:36%'>Concrete element</th><th class='left' style='width:25%'>Applicable levels</th><th>Foundation (FO)</th><th>Superstructure (S)</th><th>Weight</th>" + other_head + "</tr></thead><tbody>"]
    for r in rows:
        cells.append(f"<tr><td>{h(r['letter'])}</td><td class='left'>{h(r['element'].title())}</td>"
                     f"<td class='left'>{h(r['level_range'].title() or '—')}</td>"
                     f"<td class='number'>{join_values(r['FO'])}</td><td class='number'>{join_values(r['S'])}</td><td>{join_values(r['weights'], 'text')}</td>"
                     + (f"<td class='number'>{join_values(r.get('Other', []))}</td>" if unknown else "") + "</tr>")
    cells.append("</tbody></table>")
    st.markdown("".join(cells), unsafe_allow_html=True)
    if any(len(r[grp]) > 1 for r in rows for grp in ("FO", "S")):
        st.caption("Multiple values were found for the same element and floor range; check the listed drawing sources.")


def cover_table(records):
    rows = comparable_rows(records, "covers")
    if not rows:
        st.info("No readable concrete-cover schedule was detected.")
        return
    st.markdown("<div class='section-heading'><strong>Minimum Concrete Cover</strong><span class='small-status'>As noted in the drawings</span></div>", unsafe_allow_html=True)
    st.markdown(source_labels(records), unsafe_allow_html=True)
    unknown = any(r.get("Other") for r in rows)
    other_head = "<th>Other drawings</th>" if unknown else ""
    cells = ["<table class='schedule-table'><thead><tr><th style='width:7%'>Ref.</th><th class='left'>Concrete element / exposure</th><th style='width:25%'>Foundation (FO)</th><th style='width:25%'>Superstructure (S)</th>" + other_head + "</tr></thead><tbody>"]
    for r in rows:
        cells.append(f"<tr><td>{h(r['letter'])}</td><td class='left'>{h(r['element'].title())}</td>"
                     f"<td class='number'>{join_values(r['FO'], 'text')}</td><td class='number'>{join_values(r['S'], 'text')}</td>"
                     + (f"<td class='number'>{join_values(r.get('Other', []), 'text')}</td>" if unknown else "") + "</tr>")
    cells.append("</tbody></table>")
    st.markdown("".join(cells), unsafe_allow_html=True)


def rebar_schedule_table(records, schedule):
    if not records:
        st.info(f"No {schedule.lower()} schedule was found for this drawing set.")
        return
    strengths=sorted({r["concrete_psi"] for r in records})
    grouped = defaultdict(dict)
    for r in records:
        grouped[r["bar_size"]][r["concrete_psi"]] = r
    bar_list = sorted(grouped, key=lambda s: int(s.lstrip("#")))
    hook = schedule == "Hooked development"
    headers = ("<tr><th rowspan='2'>Bar</th>" + "".join(f"<th colspan='{2 if hook else 3}'>{v:,} psi</th>" for v in strengths) + "</tr>")
    headers += "<tr>" + (("<th>Ldh</th><th>Ext.</th>" * len(strengths)) if hook else ("<th>Typ.</th><th>Top</th><th>Comp.</th>" * len(strengths))) + "</tr>"
    body = []
    fields = ("tension_typ_in", "hook_extension_in") if hook else ("tension_typ_in", "tension_top_in", "compression_in")
    for bar in bar_list:
        row = [f"<tr><td class='number'>{h(bar)}</td>"]
        for psi in strengths:
            item = grouped[bar].get(psi)
            for field in fields:
                val = item.get(field) if item else None
                row.append(f"<td>{h(val) if val is not None else '—'}</td>")
        row.append("</tr>")
        body.append("".join(row))
    st.markdown("<div class='section-heading'><strong>" + h(schedule) + " lengths</strong><span class='small-status'>All dimensions in inches</span></div>", unsafe_allow_html=True)
    st.markdown("<table class='schedule-table'><thead>" + headers + "</thead><tbody>" + "".join(body) + "</tbody></table>", unsafe_allow_html=True)
    if schedule == "Lap splice":
        st.markdown("<div class='cover-foot'>Typ. / Top = tension splice lengths; Comp. = compression splice length. For larger bars, follow the project-specific splice requirements.</div>", unsafe_allow_html=True)
    elif schedule == "Straight development":
        st.markdown("<div class='cover-foot'>Typ. / Top = tension development lengths; Comp. = compression development length.</div>", unsafe_allow_html=True)
    else:
        st.markdown("<div class='cover-foot'>Ldh = hooked tension development length; Ext. = extension. Verify hook type and associated drawing notes.</div>", unsafe_allow_html=True)


def excel_report(result):
    output = io.BytesIO()
    strengths = comparable_rows(result["strengths"], "strengths")
    covers = comparable_rows(result["covers"], "covers")
    def comparison_df(rows, kind):
        return pd.DataFrame([{"ref": r["letter"], "element": r["element"],
                              **({"levels": r["level_range"]} if kind == "strengths" else {}),
                              "foundation_FO": join_values(r["FO"], "number" if kind == "strengths" else "text"),
                              "superstructure_S": join_values(r["S"], "number" if kind == "strengths" else "text")}
                             for r in rows])
    sheets = {
        "Strength comparison": comparison_df(strengths, "strengths"),
        "Cover comparison": comparison_df(covers, "covers"),
        "Strength source data": pd.DataFrame(result["strengths"]),
        "Cover source data": pd.DataFrame(result["covers"]),
        "Rebar schedule data": pd.DataFrame(result["rebar_schedules"]),
        "Additional strength notes": pd.DataFrame(result["other_strength_notes"]),
        "QA warnings": pd.DataFrame(result["qa_warnings"]),
    }
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        for name, df in sheets.items():
            if df.empty:
                df = pd.DataFrame({"message": ["No records detected"]})
            df.to_excel(writer, sheet_name=name[:31], index=False)
            ws = writer.sheets[name[:31]]
            ws.freeze_panes = "A2"
            ws.auto_filter.ref = ws.dimensions
            for col in ws.columns:
                ws.column_dimensions[col[0].column_letter].width = min(42, max(12, max(len(str(cell.value or "")) for cell in list(col)[:100]) + 2))
    return output.getvalue()


def run_app():
    st.set_page_config(page_title="Structural Drawing Analyzer | Elevated Engineering NYC", layout="wide", page_icon="🏗️")
    st.markdown(CSS, unsafe_allow_html=True)
    st.markdown("""<div class='app-hero'>
<div class='app-kicker'>Elevated Engineering NYC · Structural Design</div>
<div class='app-title'>Structural Drawing Analyzer</div>
<div class='app-subtitle'>Drawing-source strength and cover schedules · Reinforcement lengths · Review flags</div>
</div>""", unsafe_allow_html=True)

    files = st.file_uploader("Upload foundation and superstructure drawing PDFs", type="pdf", accept_multiple_files=True)
    go = st.button("Extract drawing schedules", type="primary")
    signature = [(f.name, f.size) for f in files] if files else []
    if go:
        if not files:
            st.info("Upload one or more drawing PDFs first.")
        else:
            with st.spinner("Extracting drawing schedules…"):
                try:
                    st.session_state["v7_result"] = extract_files(files)
                    st.session_state["v7_signature"] = signature
                except Exception as exc:
                    st.error(f"Unable to analyze the uploaded PDF: {exc}")

    if "v7_result" in st.session_state and st.session_state.get("v7_signature") != signature:
        st.info("Files changed. Click Extract drawing schedules to refresh the results.")
    elif "v7_result" in st.session_state:
        data = st.session_state["v7_result"]
        strength_count = len(comparable_rows(data["strengths"], "strengths"))
        cover_count = len(comparable_rows(data["covers"], "covers"))
        st.markdown(f"<div class='source-line'>{data['file_count']} PDF(s) · {data['page_count']} pages · {strength_count} strength rows · {cover_count} cover rows</div>", unsafe_allow_html=True)
        tab_strength, tab_cover, tab_rebar, tab_export = st.tabs([
            "Concrete strengths", "Concrete covers", "Lap & development", "Review & export"
        ])
        with tab_strength:
            strength_table(data["strengths"])
        with tab_cover:
            cover_table(data["covers"])
        with tab_rebar:
            schedules = data["rebar_schedules"]
            sources = []
            for typ in ("S", "FO", "Other"):
                if any(discipline(r["sheet"]) == typ for r in schedules):
                    sources.append(typ)
            if sources:
                labels = {"S": "Superstructure (S)", "FO": "Foundation (FO)", "Other": "Other drawing sheets"}
                source = st.radio("Drawing set", sources, format_func=lambda x: labels[x], horizontal=True)
                specific = [r for r in schedules if discipline(r["sheet"]) == source]
                st.markdown(source_labels(specific), unsafe_allow_html=True)
                t1, t2, t3 = st.tabs(["Lap splice", "Straight development", "Hooked development"])
                for tab, kind in zip((t1, t2, t3), ("Lap splice", "Straight development", "Hooked development")):
                    with tab:
                        rebar_schedule_table([r for r in specific if r["schedule"] == kind], kind)
            else:
                st.info("No reinforcement schedules could be extracted. This does not mean the drawings have no reinforcement requirements; inspect the Review & export tab and the original sheets.")
        with tab_export:
            st.markdown("**Download extracted schedules**")
            left, right = st.columns(2)
            with left:
                st.download_button("Download Excel workbook", excel_report(data),
                                   file_name="structural_analyzer_v7.xlsx",
                                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            with right:
                st.download_button("Download JSON", json.dumps(data, indent=2),
                                   file_name="structural_analyzer_v7.json", mime="application/json")
            if data["other_strength_notes"]:
                with st.expander(f"Other compressive-strength notes ({len(data['other_strength_notes'])})"):
                    for r in data["other_strength_notes"]:
                        st.markdown(f"**{h(r['sheet'])} · p.{r['page']}** — {h(r['note'])}")
            with st.expander(f"Extraction QA ({len(data['qa_warnings'])} warnings)"):
                if data["qa_warnings"]:
                    for r in data["qa_warnings"]:
                        st.caption(f"{r['sheet']} / page {r['page']}: {r['warning']}")
                else:
                    st.caption("No missing table cells detected. Always verify against the drawing originals.")
            st.caption("This tool reads embedded PDF text. It cannot guarantee accuracy for all drawing formats. Scanned sheets, unusual layouts, and conflicting notes require verification.")
    st.markdown("<div class='app-footer'>Developed by <strong>Elevated Engineering NYC</strong> · Structural Drawing Analyzer</div>", unsafe_allow_html=True)


if __name__ == "__main__":
    run_app()
