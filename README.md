# Structural PDF Extractor V3

Install: `pip install -r requirements.txt`

Run: `streamlit run app.py`

Upload one or more structural/foundation drawing PDFs. Download Excel or JSON.

This version handles PDF embedded text and fixed-layout schedules resembling the supplied 202 East 120th Street drawings. It flags missing table cells. It does **not** guarantee 100% coverage of arbitrary layouts, scans or superseding drawing notes; check against originals.
