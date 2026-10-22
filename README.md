# Structural & Foundation Drawing Analyzer — Version 2

## Updating from V1 (Windows)

1. Keep a backup copy of your V1 folder.
2. Extract this ZIP and open its folder containing `app.py`.
3. Click File Explorer's address bar, type `cmd`, and press Enter.
4. Run `py -m pip install -r requirements.txt` (safe to re-run after V1).
5. Run `py -m streamlit run app.py`.
6. Open http://localhost:8501 if it does not open automatically.

If V1 is already running, stop it first by clicking its Command Prompt and pressing Ctrl+C. Do not run both on the same port.

## Features
- Upload multiple structural/foundation drawing PDFs
- Count identified stories and floor framing, roof/bulkhead plan sheets separately
- Extract and deduplicate 28-day concrete compressive strengths and retain every source
- Flag differing general footing note and specific footing schedule without falsely resolving it
- Show note-based foundation and structural component mentions, explicitly marked for plan verification
- List referenced drawing index sheets
- Preview a chosen original PDF page at readable resolution
- Export a paginated PDF summary report, full JSON, concrete strengths CSV, plan index CSV, and system references CSV

## Cautions
- Works best on PDFs containing selectable vector text; scanned sheets need OCR, not in V2.
- No guaranteed extraction of every drawing note or detail, and no plan geometry interpretation.
- Foundation/structural system entries report mentions, NOT verified selected systems.
- Story count is based on language such as 'NEW 14-STORY', not inferred from graphical elevations.
- Engineering specifications should always be checked against issued drawings and with the EOR.
