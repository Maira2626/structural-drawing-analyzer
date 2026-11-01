# Structural Drawing Analyzer — V7 (corrective update)

**Purpose:** Extract concrete strength, cover, and reinforcement schedules from structural drawing PDFs and show them as compact, source-referenced tables.

## Important limitation
V7 is a more adaptive parser, **not a universal 100%-accurate extractor**. A PDF's text encoding/layout may differ significantly from what its on-screen drawing looks like. Scanned or outlined text is not automatically recognized; no extracted value should be used for structural design without verifying the source sheet.

## Changes from V6
- Strength parser uses geometry and actual PSI text rather than a preset category list, project name, or 720-pixel table width.
- Processes multiline elements, floor ranges within parentheses, and indented floor lines.
- Cover parser similarly uses source text positions to read the schedule.
- Ignores unrelated lettered tables and keeps source page references.
- Flags pages that mention compressive strength but do not yield a structured schedule.
- Rebar panel handles unclassified sheet names (in addition to FO and S), and reports unsupported layouts rather than silently substituting values.
- Retains compact strength/cover and lap/development displays and the Elevated Engineering NYC footer.

## Tests performed
- Both uploaded 202 East 120th Street files (43 pages total): 9 strengths in each general-note schedule, 7 cover rows in each, and 168 reinforcement schedule entries total.
- Four synthetic text PDFs modeled after the supplied alternate-project screenshots: 6/6, 9/9, 6/6, and 6/6 strength values.
- These tests **do not validate actual other-project PDFs**, which were not supplied. For a failure, upload the actual failing PDF and expected schedule screenshot so we can test the exact encoding/layout.

## Installation / website update
Your existing Streamlit deployment can use the same GitHub repository and URL.

1. Extract the ZIP.
2. In the repo root upload or replace **all seven files**:
   - `app.py`
   - `adaptive.py` **(new and required)**
   - `extractor.py`
   - `rebar.py`
   - `requirements.txt`
   - `README.md`
   - `test_extractor.py`
3. Commit to `main`, allow Streamlit to deploy, and reload the website.

Run locally:

```bash
python -m pip install -r requirements.txt
streamlit run app.py
```

Tests:

```bash
python -m unittest test_extractor -v
```

## Why a drawing can still fail
- Scanned image only, no embedded text layer
- Text represented by exploded glyphs or unusual font encoding
- Strength values without units under a separate column header
- Large multi-column notes with rotated/reversed text
- Reinforcement tables that don't use recognizable headings and grid structures

A missing result is **not** evidence that the drawing lacks the requirement. Inspect the original and Review & export tab.
