"""Structural / Foundation PDF Analyzer V2 — auditable, rule-based extraction.

Only observations in selectable PDF text are reported. Keyword hits in sheet titles
are labeled as drawing references, not proof that a system was designed or used.
"""
import csv
import io
import json
import re
from collections import defaultdict

import fitz
import streamlit as st
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether

st.set_page_config(page_title="Structural & Foundation Drawing Analyzer V2", page_icon="🏗️", layout="wide")

STRENGTH_PATTERNS = [
    ("Slab on grade", r"SLAB ON GRADE\s*:\s*([\d,]+)\s*PSI"),
    ("Foundation walls, shear walls, columns, footings, grade beams", r"FOUNDATION WALLS\s*,\s*SHEAR WALLS\s*,?\s*COLUMNS\s*,\s*FOOTINGS\s*,\s*GRADE BEAMS\s*:\s*([\d,]+)\s*PSI"),
    ("Framed slabs and beams", r"FRAMED SLABS AND BEAMS\s*:\s*([\d,]+)\s*PSI"),
    ("Concrete topping and pads", r"CONCRETE TOPPING AND PAD\s*:\s*([\d,]+)\s*PSI"),
    ("Other nonstructural elements", r"OTHER NON STRUCTURAL ELEMENTS\s*:\s*([\d,]+)\s*PSI"),
    ("All other concrete", r"ALL OTHER CONCRETE\s*:?\s*([\d,]+)\s*PSI"),
]

SYSTEM_TERMS = {
    "Mat foundation": [r"\bMAT FOUNDATION\b"],
    "Pile caps": [r"\bPILE CAP\b"],
    "Deep foundations / piles": [r"\b(?:PILE|PILES|DEEP FOUNDATION ELEMENTS)\b"],
    "Isolated footings": [r"\bISOLATED FOOTINGS?\b", r"\bSPREAD FOOTINGS?\b"],
    "Grade beams": [r"\bGRADE BEAMS?\b"],
    "Shear walls": [r"\bSHEAR WALLS?\b"],
    "Concrete columns": [r"\bCOLUMN SCHEDULE\b", r"\bCONCRETE COLUMNS?\b"],
    "Framed concrete slabs": [r"\bFRAMED SLABS\b", r"\bSLAB REINFORCEMENT\b"],
}


def flatten(value):
    return re.sub(r"\s+", " ", value.upper()).strip()


def snippet(text, start, end, width=60):
    return text[max(0, start - width):min(len(text), end + width)].strip()


def pdf_data(uploads):
    entries, strengths, plans, story_claims, evidence, warnings = [], [], [], [], [], []
    for upload in uploads:
        name = upload.name
        try:
            doc = fitz.open(stream=upload.getvalue(), filetype="pdf")
            if doc.needs_pass:
                warnings.append(f"{name}: password-protected PDF; could not inspect.")
                continue
        except Exception as error:
            warnings.append(f"{name}: failed to open ({error}).")
            continue
        entries.append({"file": name, "pages": len(doc)})
        first_page_text = ""
        total_chars = 0
        for index, pg in enumerate(doc):
            raw = pg.get_text()
            norm = flatten(raw)
            total_chars += len(norm)
            if index == 0:
                first_page_text = raw
            for match in re.finditer(r"\bNEW\s+(\d{1,2})\s*[- ]?STOR(?:Y|IES)\b", norm):
                value = int(match.group(1))
                story_claims.append({"stories": value, "file": name, "page": index + 1, "evidence": snippet(norm, match.start(), match.end())})
            for label, pattern in STRENGTH_PATTERNS:
                if "CAST-IN-PLACE CONCRETE" not in norm and label != "Slab on grade":
                    continue
                match = re.search(pattern, norm)
                if match:
                    strengths.append({"element": label, "psi": int(match.group(1).replace(",", "")),
                                      "file": name, "page": index + 1, "sheet": "", "source": "Concrete strength schedule",
                                      "evidence": match.group(0)})
            general = re.search(r"CONCRETE FOR FOOTINGS,? PIERS,? AND SLABS ON GRADE SHALL HAVE A MINIMUM\s+COMPRESSIVE STRENGTH OF\s*([\d,]+)\s*[- ]?PSI", norm)
            if general:
                strengths.append({"element": "Footings, piers, slabs on grade (general foundation note)",
                                  "psi": int(general.group(1).replace(",", "")), "file": name, "page": index + 1,
                                  "sheet": "", "source": "General foundation note (unless otherwise noted)",
                                  "evidence": general.group(0)})
            # Evidence is limited to drawing titles / schedules and is not intended
            # as confirmation that a particular system appears on plans.
            for system, patterns in SYSTEM_TERMS.items():
                for pattern in patterns:
                    match = re.search(pattern, norm)
                    if match:
                        evidence.append({"system": system, "file": name, "page": index + 1,
                                         "evidence": snippet(norm, match.start(), match.end(), 35),
                                         "status": "Mentioned in notes/schedules; verify on plans"})
                        break
        if total_chars < 250:
            warnings.append(f"{name}: very little selectable text. OCR for scanned sheets is not included.")
        # Index is a structured listing in these drawing sets. Avoid treating
        # every mention of a floor as a separate drawing.
        pat = re.compile(r"(?m)^\s*(\d{1,2})\s*\n\s*((?:S|FO)-\d{3}\.\d{2})\s*\n\s*([^\n]+)")
        for match in pat.finditer(first_page_text):
            sheet_title = match.group(3).strip()
            if any(key in sheet_title.upper() for key in ("FLOOR FRAMING PLAN", "ROOF FRAMING PLAN", "BULKHEAD FRAMING PLAN", "FOUNDATION PLAN", "PILE CAP", "MAT FOUNDATION", "COLUMN SCHEDULE", "SHEAR WALL SCHEDULE")):
                plans.append({"file": name, "page": int(match.group(1)), "sheet": match.group(2), "title": sheet_title})
        doc.close()
    # Deduplicate repeated text records on the exact same source page.
    strengths = list({(r["element"], r["psi"], r["file"], r["page"]): r for r in strengths}.values())
    plans = list({(p["file"], p["sheet"]): p for p in plans}.values())
    evidence = list({(e["system"], e["file"], e["page"]): e for e in evidence}.values())
    return entries, strengths, plans, story_claims, evidence, warnings


def group_strengths(rows):
    grouped = defaultdict(list)
    for r in rows:
        grouped[(r["element"], r["psi"])].append(r)
    combined = []
    for (element, psi), sources in grouped.items():
        combined.append({"element": element, "psi": psi,
                         "sources": "; ".join(f'{s["file"]} · page {s["page"]}' for s in sources),
                         "source_count": len(sources)})
    return sorted(combined, key=lambda r: (r["element"], r["psi"]))


def detect_conflicts(rows):
    issues = []
    general = [r for r in rows if r["element"].startswith("Footings, piers")]
    footings = [r for r in rows if "columns, footings, grade beams" in r["element"].lower()]
    differing = [(a, b) for a in general for b in footings if a["psi"] != b["psi"]]
    if differing:
        general_refs = "; ".join(sorted({f'{a["file"]} page {a["page"]} ({a["psi"]:,} psi)' for a, _ in differing}))
        schedule_refs = "; ".join(sorted({f'{b["file"]} page {b["page"]} ({b["psi"]:,} psi)' for _, b in differing}))
        issues.append("Footing-strength note difference: general note " + general_refs +
                      "; cast-in-place schedule " + schedule_refs +
                      '. The general note states “unless otherwise noted.” Confirm which requirement governs with the drawings/EOR; not automatically resolved.')
    # Any different strengths for an exact same element label are also highlighted.
    by_element = defaultdict(set)
    for r in rows:
        by_element[r["element"]].add(r["psi"])
    for element, vals in by_element.items():
        if len(vals) > 1:
            issues.append(f'{element}: multiple listed strengths ({", ".join(f"{v:,}" for v in sorted(vals))} psi). Check revisions, notes and detail overrides.')
    return issues


def csv_blob(rows):
    if not rows:
        return b""
    f = io.StringIO()
    writer = csv.DictWriter(f, fieldnames=rows[0].keys())
    writer.writeheader()
    writer.writerows(rows)
    return f.getvalue().encode("utf-8-sig")


def pdf_report(report):
    """Create a readable, multi-page summary entirely in memory."""
    from xml.sax.saxutils import escape
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, rightMargin=42, leftMargin=42,
                            topMargin=45, bottomMargin=44, title="Structural Drawing Analysis")
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="ReportTitle", parent=styles["Title"], fontSize=17, leading=22,
                              textColor=colors.HexColor("#183252"), spaceAfter=10))
    styles.add(ParagraphStyle(name="ReportSection", parent=styles["Heading2"], fontSize=11,
                              leading=14, textColor=colors.HexColor("#183252"), spaceBefore=13, spaceAfter=6))
    styles.add(ParagraphStyle(name="ReportCell", parent=styles["Normal"], fontSize=7, leading=10))
    styles.add(ParagraphStyle(name="ReportSmall", parent=styles["Normal"], fontSize=8, leading=12))
    story_nums = {r["stories"] for r in report["stories"]}
    story_label = str(next(iter(story_nums))) if len(story_nums) == 1 else ("Review conflicting entries" if story_nums else "Not found")
    content = [Paragraph("Structural & Foundation Drawing Analysis", styles["ReportTitle"]),
               Paragraph("Rule-based text extraction; review original drawings and confirm discrepancies with the engineer of record.", styles["ReportSmall"])]
    def heading(text):
        content.append(Paragraph(escape(text), styles["ReportSection"]))
    def tbl(headers, data, widths):
        def cell(x):
            return Paragraph(escape(str(x if x is not None else "")).replace("\n", "<br/>"), styles["ReportCell"])
        rows = [[cell(v) for v in headers]] + [[cell(v) for v in row] for row in data]
        t = Table(rows, colWidths=widths, repeatRows=1, hAlign="LEFT")
        t.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#e3eaf2")),
            ("VALIGN", (0,0), (-1,-1), "TOP"),
            ("GRID", (0,0), (-1,-1), .3, colors.HexColor("#cbd5e1")),
            ("LEFTPADDING", (0,0), (-1,-1), 6), ("RIGHTPADDING", (0,0), (-1,-1), 6),
            ("TOPPADDING", (0,0), (-1,-1), 5), ("BOTTOMPADDING", (0,0), (-1,-1), 5),
        ]))
        content.append(t)
    heading("Project overview")
    tbl(["Parameter", "Result"], [
        ("Stories identified", story_label),
        ("Floor framing plan sheets", len(report["floor_framing_sheets"])),
        ("Roof / bulkhead plan sheets", len(report["roof_bulkhead_sheets"])),
        ("Uploaded PDF pages", sum(f["pages"] for f in report["files"])),
    ], [210, 318])
    heading("Uploaded drawing sets")
    tbl(["File", "PDF pages"], [(r["file"], r["pages"]) for r in report["files"]], [442,86])
    heading("Concrete strengths - 28-day f'c")
    strengths = report["strength_summary"]
    if strengths:
        tbl(["Element", "psi", "Sources / evidence"], [
            (r.get("element",""), f'{r.get("psi", ""):,}' if isinstance(r.get("psi"),int) else r.get("psi",""),
             "; ".join(f'{k}: {v}' for k,v in r.items() if k not in ("element","psi")))
            for r in strengths], [178,49,301])
    else:
        content.append(Paragraph("No concrete strengths found in selectable text.", styles["ReportSmall"]))
    heading("Foundation and structural system references")
    evid = report["system_references"]
    if evid:
        by_system = defaultdict(list)
        for r in evid:
            by_system[r["system"]].append(r)
        tbl(["Mentioned system", "First source", "Status"], [
            (name, f'{rows[0]["file"]} - PDF p. {rows[0]["page"]}', "Mentioned; verify on plans")
            for name, rows in sorted(by_system.items())], [132,265,131])
    else:
        content.append(Paragraph("No system references found.", styles["ReportSmall"]))
    heading("Drawing index")
    plans = report["drawing_index"]
    if plans:
        tbl(["Sheet", "Title", "Source"], [
            (r.get("sheet", r.get("drawing", "")), r.get("title", ""),
             f'{r.get("file", "")} - PDF p. {r.get("page", "")}') for r in plans], [81,220,227])
    else:
        content.append(Paragraph("No drawing index entries found.", styles["ReportSmall"]))
    heading("Warnings and verification")
    if report["warnings"]:
        for msg in report["warnings"]:
            content.append(Paragraph("- " + escape(str(msg)), styles["ReportSmall"]))
            content.append(Spacer(1, 5))
    else:
        content.append(Paragraph("No predefined warnings triggered. This does not establish drawing approval.", styles["ReportSmall"]))
    def footer(canvas, pdfdoc):
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.grey)
        canvas.drawString(42, 28, "Extracted summary - verify against the issued drawings")
        canvas.drawRightString(letter[0]-42, 28, f"Page {pdfdoc.page}")
    doc.build(content, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()


st.title("Structural & Foundation Drawing Analyzer")
st.caption("Version 2 · Project summary + detailed evidence · PDF page preview")
st.info("Rule-based extraction from selectable PDF text. Drawing titles and note mentions do not prove construction systems; inspect the actual plans and ask the EOR about ambiguous specifications.")
uploads = st.file_uploader("Upload structural and foundation drawing sets (PDF)", type="pdf", accept_multiple_files=True)
if uploads:
    with st.spinner("Analyzing notes and drawing indexes..."):
        file_info, raw_strengths, plans, stories, system_evidence, warnings = pdf_data(uploads)
    grouped_strengths = group_strengths(raw_strengths)
    conflicts = detect_conflicts(raw_strengths)
    floor_plans = [p for p in plans if "FLOOR FRAMING PLAN" in p["title"].upper()]
    roof_plans = [p for p in plans if any(x in p["title"].upper() for x in ("ROOF FRAMING PLAN", "BULKHEAD FRAMING PLAN"))]
    story_values = {s["stories"] for s in stories}
    st.subheader("Project overview")
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Stories identified", next(iter(story_values)) if len(story_values) == 1 else ("Review" if story_values else "Not found"))
    col2.metric("Floor framing plan sheets", len(floor_plans))
    col3.metric("Roof / bulkhead sheets", len(roof_plans))
    col4.metric("PDF pages", sum(r["pages"] for r in file_info))
    st.caption("Number of framing-plan sheets is not the number of floors; a typical plan may cover multiple levels. Basement count is not inferred unless explicitly documented.")
    if len(story_values) > 1:
        st.warning("Different story counts were found: " + ", ".join(map(str, sorted(story_values))))
    if stories:
        with st.expander("Story-count evidence"):
            st.dataframe(stories, hide_index=True, use_container_width=True)
    st.subheader("Concrete compressive strength — 28-day f'c")
    st.dataframe(grouped_strengths, use_container_width=True, hide_index=True)
    with st.expander("Show each original concrete note / citation"):
        st.dataframe(raw_strengths, use_container_width=True, hide_index=True)
    st.subheader("Foundation & structural systems — references to review")
    st.caption("These are keyword mentions, not verified as-built or design-system classifications. A title like 'MAT FOUNDATION DETAIL' alone does not confirm a mat was selected for the entire project.")
    system_rows = []
    by_system = defaultdict(list)
    for row in system_evidence:
        by_system[row["system"]].append(row)
    for label, rows in sorted(by_system.items()):
        system_rows.append({"system / element": label, "status": "Mentioned — plan verification needed",
                            "first reference": f'{rows[0]["file"]} · page {rows[0]["page"]}',
                            "matching pages": len(rows)})
    if system_rows:
        st.dataframe(system_rows, hide_index=True, use_container_width=True)
        with st.expander("System mentions and supporting text"):
            st.dataframe(system_evidence, hide_index=True, use_container_width=True)
    else:
        st.write("No matching system references found in selectable text.")
    st.subheader("Drawing index")
    if plans:
        st.dataframe(plans, hide_index=True, use_container_width=True)
    else:
        st.write("No matching plan entries were found; inspect drawing indexes manually.")
    st.subheader("Warnings & verification")
    if conflicts:
        for issue in conflicts:
            st.warning(issue)
    else:
        st.success("No predefined concrete-strength conflicts detected (not an engineering approval).")
    for message in warnings:
        st.warning(message)
    st.subheader("Source PDF page preview")
    source_names = [u.name for u in uploads]
    chosen_name = st.selectbox("PDF", source_names)
    chosen_file = next(u for u in uploads if u.name == chosen_name)
    preview_doc = fitz.open(stream=chosen_file.getvalue(), filetype="pdf")
    page_no = st.number_input("PDF page (1-based)", min_value=1, max_value=len(preview_doc), value=1, step=1)
    st.caption("Use the file and page from the table above to inspect the original drawing. Preview locations are approximate; this version does not highlight individual callouts.")
    pix = preview_doc[int(page_no)-1].get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
    st.image(pix.tobytes("png"), caption=f"{chosen_name} — page {page_no}", use_container_width=True)
    preview_doc.close()
    st.subheader("Export data")
    report = {"files": file_info, "stories": stories, "floor_framing_sheets": floor_plans,
              "roof_bulkhead_sheets": roof_plans, "strength_summary": grouped_strengths,
              "strength_evidence": raw_strengths, "system_references": system_evidence,
              "drawing_index": plans, "warnings": conflicts + warnings}
    st.download_button("Download PDF report", pdf_report(report), "structural_drawing_summary_v2.pdf", "application/pdf", type="primary")
    c1, c2, c3, c4 = st.columns(4)
    c1.download_button("Full report (JSON)", json.dumps(report, indent=2).encode(), "structural_summary_v2.json", "application/json")
    c2.download_button("Strengths (CSV)", csv_blob(grouped_strengths), "concrete_strength_summary.csv", "text/csv", disabled=not grouped_strengths)
    c3.download_button("Drawing index (CSV)", csv_blob(plans), "drawing_index.csv", "text/csv", disabled=not plans)
    c4.download_button("System refs (CSV)", csv_blob(system_rows), "system_references.csv", "text/csv", disabled=not system_rows)
else:
    st.write("Upload your PDFs to receive a source-linked project summary and browse the original drawing pages.")
