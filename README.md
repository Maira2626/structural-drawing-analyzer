# Structural Drawing Analyzer V6 — Generalized schedules

Developed by Elevated Engineering NYC.

## What changed

**Concrete compressive strengths:** The extractor no longer assumes a particular project, A–G categories, nine rows, 4/6/8/10 ksi values, or floor ranges. It finds positioned text beneath a concrete compressive strength note and matches each lettered entry to its PSI value. It also recognizes:

- A–F and A–I schedules, and schedules without the full heading (flagged for review)
- Floor ranges beside the strength, indented underneath a category, or in parentheses beside the element
- Wrapped descriptions, including multiple elements sharing a single strength
- Arbitrary concrete strengths, not just 4,000/6,000/8,000/10,000 psi
- Foundation/superstructure comparisons based on category + floor range, not the A/B/C letter (which can differ between sets)

**Lap and development schedules:** The parser reads column strength headings and numeric row values from PDF geometry rather than assuming 6,000/8,000/10,000 psi or fixed distances between columns. The hooked tension development columns are read separately from hook extensions. Unsupported table configurations are flagged instead of being filled with guessed values. The schedule table layout currently supports the rectangular formats illustrated by the supplied drawings; other patterns may need extension.

**Other functionality:** Compact website remains, including concrete cover schedules, drawing/page references, note-conflict alerts and Excel/JSON downloads.

## Limitations

No extractor can guarantee 100% accuracy across all possible engineering plans. This version is designed for **text-searchable (native-text) PDFs**, and may miss details drawn as raster images, outlined vector shapes, CAD text with unusual ordering, or irregular table layouts. It **does not perform OCR**. The original drawing and engineer's interpretation always take precedence. If a sheet returns no values or a table is unsupported, review the PDF manually. Where no heading is detected, inferred schedules are labeled and flagged.

## Install locally

```bash
python -m pip install -r requirements.txt
streamlit run app.py
```

## Update your existing GitHub/Streamlit site

1. Extract the ZIP.
2. Upload all **five application files** into the root of `Maira2626/structural-drawing-analyzer`, replacing existing files when prompted:
   - `app.py`
   - `extractor.py` **(new; required)**
   - `rebar.py` **(new; required)**
   - `requirements.txt`
   - `README.md`
3. Commit directly to the `main` branch.
4. Streamlit Cloud should redeploy from GitHub automatically.

`test_extractor.py` is optional; keep it for reproducible tests if desired.

## Validation

The generalized text parser was tested on synthetic text-based PDFs reproducing the four supplied schedule patterns and on both original 202 East 120th Street drawing sets. These are regression tests for the examples, not proof of universal accuracy on unseen drawings.
