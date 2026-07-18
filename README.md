# PDF Tool — Combine & Compress PDFs

Free, local PDF tool. No watermarks, no cloud uploads, no limits.

## Prerequisites

- **Python 3.9+**
- **Ghostscript** (required for compression)
  - **Windows:** Download from [ghostscript.com/releases](https://ghostscript.com/releases/gsdnld.html), install, and ensure `gswin64c` is on your PATH
  - **Mac:** `brew install ghostscript`
  - **Linux:** `sudo apt install ghostscript`

## Quick Start

```bash
# Install Python dependencies
cd backend
pip install -r ../requirements.txt

# Start the server
uvicorn main:app --reload
```

Open **http://localhost:8000** in your browser. That's it.

## Features

- **Combine PDFs** — merge up to 50 PDFs, drag to reorder
- **Compress PDF** — three levels (Low/Medium/High) via Ghostscript
- Auto-download result with browser Save As dialog
- 200 MB per-file limit
- Files auto-deleted after download

## File Structure

```
├── backend/
│   └── main.py          ← entire backend + static file serving
├── frontend/
│   ├── index.html       ← UI structure
│   ├── style.css        ← styles
│   └── app.js           ← all logic
├── requirements.txt
└── README.md
```
