"""PDF Tool — FastAPI backend for combine & compress."""

import shutil
import subprocess
import platform
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pypdf import PdfReader, PdfWriter
from starlette.background import BackgroundTask

# ──── Config ────

BASE_DIR = Path(__file__).parent
DOWNLOADS_DIR = BASE_DIR / "downloads"
TEMP_DIR = BASE_DIR / "temp"
FRONTEND_DIR = BASE_DIR.parent / "frontend"
MAX_FILE_SIZE = 200 * 1024 * 1024  # 200 MB
MAX_FILES = 50

def find_ghostscript() -> str:
    default_name = "gswin64c" if platform.system() == "Windows" else "gs"
    if shutil.which(default_name):
        return default_name
    if platform.system() == "Windows":
        if shutil.which("gswin32c"):
            return "gswin32c"
        # Check standard installation locations
        for base in [Path("C:/Program Files/gs"), Path("C:/Program Files (x86)/gs")]:
            if base.exists():
                for sub in base.iterdir():
                    if sub.is_dir() and sub.name.startswith("gs"):
                        bin_dir = sub / "bin"
                        for exe in ["gswin64c.exe", "gswin32c.exe"]:
                            candidate = bin_dir / exe
                            if candidate.exists():
                                return str(candidate)
    return default_name

GS_CMD = find_ghostscript()
COMPRESSION_MAP = {"low": "/printer", "medium": "/ebook", "high": "/screen"}

# ponytail: in-memory metadata, lost on restart — fine for ephemeral download IDs
_download_names: dict[str, str] = {}


# ──── App ────

@asynccontextmanager
async def lifespan(_app):
    DOWNLOADS_DIR.mkdir(exist_ok=True)
    TEMP_DIR.mkdir(exist_ok=True)
    # Check Ghostscript availability
    try:
        subprocess.run([GS_CMD, "--version"], capture_output=True, timeout=5, check=False)
        print(f"[OK] Ghostscript found at: {GS_CMD}", flush=True)
    except FileNotFoundError:
        print(f"[WARNING] Ghostscript not found -- compression will fail.", flush=True)
        print("  Install: https://ghostscript.com/releases/gsdnld.html", flush=True)
    yield


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # for separate dev server if needed
    allow_methods=["*"],
    allow_headers=["*"],
)


# ──── Helpers ────

def _validate_pdf(f: UploadFile):
    if not f.filename or not f.filename.lower().endswith(".pdf"):
        raise HTTPException(400, detail=f"'{f.filename}' is not a PDF file")


# ──── Endpoints ────

@app.post("/api/combine")
async def combine_pdfs(files: list[UploadFile] = File(...)):
    if len(files) < 2:
        raise HTTPException(400, detail="Need at least 2 PDFs to combine")
    if len(files) > MAX_FILES:
        raise HTTPException(400, detail=f"Maximum {MAX_FILES} files allowed")

    temp_paths: list[Path] = []
    try:
        # Save uploads to temp
        for f in files:
            _validate_pdf(f)
            content = await f.read()
            if len(content) > MAX_FILE_SIZE:
                raise HTTPException(400, detail=f"'{f.filename}' exceeds 200 MB limit")
            p = TEMP_DIR / f"{uuid.uuid4()}.pdf"
            p.write_bytes(content)
            temp_paths.append(p)

        # Merge
        writer = PdfWriter()
        for i, p in enumerate(temp_paths):
            try:
                reader = PdfReader(str(p))
                for page in reader.pages:
                    writer.add_page(page)
            except Exception:
                raise HTTPException(400, detail=f"'{files[i].filename}' is not a valid PDF")

        file_id = str(uuid.uuid4())
        out = DOWNLOADS_DIR / f"{file_id}.pdf"
        with open(out, "wb") as fout:
            writer.write(fout)

        _download_names[file_id] = "combined.pdf"
        return {"id": file_id, "filename": "combined.pdf", "size": out.stat().st_size}

    finally:
        for p in temp_paths:
            p.unlink(missing_ok=True)


@app.post("/api/compress")
async def compress_pdf(
    file: UploadFile = File(...),
    level: str = Form("medium"),
):
    if level not in COMPRESSION_MAP:
        raise HTTPException(400, detail=f"Invalid level '{level}'. Use: low, medium, high")
    _validate_pdf(file)

    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(400, detail="File exceeds 200 MB limit")

    original_size = len(content)
    temp_in = TEMP_DIR / f"{uuid.uuid4()}.pdf"
    file_id = str(uuid.uuid4())
    out = DOWNLOADS_DIR / f"{file_id}.pdf"

    try:
        temp_in.write_bytes(content)

        cmd = [
            GS_CMD, "-sDEVICE=pdfwrite",
            "-dCompatibilityLevel=1.4",
            f"-dPDFSETTINGS={COMPRESSION_MAP[level]}",
            "-dNOPAUSE", "-dQUIET", "-dBATCH",
            f"-sOutputFile={out}",
            str(temp_in),
        ]

        try:
            # ponytail: blocking subprocess in async endpoint; fine for single-user local tool
            result = subprocess.run(cmd, capture_output=True, timeout=120, check=False)
        except FileNotFoundError:
            raise HTTPException(
                500,
                detail="Ghostscript is not installed. "
                       "Download from https://ghostscript.com/releases/gsdnld.html",
            )
        except subprocess.TimeoutExpired:
            raise HTTPException(500, detail="Compression timed out after 120 seconds")

        if result.returncode != 0:
            stderr = result.stderr.decode(errors="replace")[:300]
            raise HTTPException(500, detail=f"Ghostscript error: {stderr}")

        compressed_size = out.stat().st_size

        # If compressed is larger or same, return the original
        if compressed_size >= original_size:
            shutil.copy2(temp_in, out)
            compressed_size = original_size

        _download_names[file_id] = "compressed.pdf"
        return {
            "id": file_id,
            "filename": "compressed.pdf",
            "original_size": original_size,
            "compressed_size": compressed_size,
        }

    finally:
        temp_in.unlink(missing_ok=True)


@app.get("/api/download/{file_id}")
async def download_file(file_id: str):
    # Prevent path traversal
    if "/" in file_id or "\\" in file_id or ".." in file_id:
        raise HTTPException(400, detail="Invalid file ID")

    path = DOWNLOADS_DIR / f"{file_id}.pdf"
    if not path.exists():
        raise HTTPException(404, detail="File not found or already downloaded")

    filename = _download_names.pop(file_id, "result.pdf")

    return FileResponse(
        path=str(path),
        filename=filename,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        background=BackgroundTask(lambda: path.unlink(missing_ok=True)),
    )


# ──── Serve frontend (must be last) ────

if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
