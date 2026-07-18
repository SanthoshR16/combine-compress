"""PDF Tool — FastAPI backend for combine & compress."""

import os
import shutil
import subprocess
import platform
import uuid
import logging
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pypdf import PdfReader, PdfWriter
from starlette.background import BackgroundTask

# ──── Logging & Config ────

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

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


# ──── Background Cleanup ────

def cleanup_loop():
    """Runs forever to clean up old temp and download files."""
    while True:
        try:
            now = time.time()
            for folder in [DOWNLOADS_DIR, TEMP_DIR]:
                if folder.exists():
                    for item in folder.iterdir():
                        if item.is_file():
                            # Delete if older than 15 minutes (900 seconds)
                            if now - item.stat().st_mtime > 900:
                                item.unlink(missing_ok=True)
                                logger.info(f"Cleaned up expired file: {item.name}")
        except Exception as e:
            logger.error(f"Error in cleanup loop: {e}")
        time.sleep(60)


# ──── App Lifespan ────

@asynccontextmanager
async def lifespan(_app):
    DOWNLOADS_DIR.mkdir(exist_ok=True)
    TEMP_DIR.mkdir(exist_ok=True)
    
    # Start background cleanup thread
    cleanup_thread = threading.Thread(target=cleanup_loop, daemon=True)
    cleanup_thread.start()
    logger.info("Background cleanup thread started.")

    # Check Ghostscript availability
    try:
        subprocess.run([GS_CMD, "--version"], capture_output=True, timeout=5, check=False)
        logger.info(f"Ghostscript found at: {GS_CMD}")
    except FileNotFoundError:
        logger.warning("Ghostscript not found -- compression will fail.")
        logger.warning("Install: https://ghostscript.com/releases/gsdnld.html")
    yield


app = FastAPI(lifespan=lifespan)

allowed_origins = os.getenv("ALLOWED_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True if allowed_origins != ["*"] else False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ──── Helpers ────

def _validate_pdf(f: UploadFile):
    if not f.filename or not f.filename.lower().endswith(".pdf"):
        raise HTTPException(400, detail=f"'{f.filename}' is not a PDF file")
    # Some browsers might send empty content_type or octet-stream for some PDFs; we validate content type if present
    if f.content_type and f.content_type != "application/pdf" and f.content_type != "application/x-pdf":
        # Double check filename extension just in case, otherwise reject
        if not f.filename.lower().endswith(".pdf"):
            raise HTTPException(400, detail=f"'{f.filename}' has invalid MIME type: {f.content_type}")

async def _read_file_safe(f: UploadFile) -> bytes:
    _validate_pdf(f)
    content = bytearray()
    chunk_size = 1024 * 1024  # 1 MB chunk
    while True:
        chunk = await f.read(chunk_size)
        if not chunk:
            break
        content.extend(chunk)
        if len(content) > MAX_FILE_SIZE:
            raise HTTPException(400, detail=f"File '{f.filename}' exceeds 200 MB limit")
    return bytes(content)


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
            content = await _read_file_safe(f)
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
            except Exception as e:
                logger.error(f"Error reading PDF {files[i].filename}: {e}")
                raise HTTPException(400, detail=f"'{files[i].filename}' is corrupted or not a valid PDF")

        file_id = str(uuid.uuid4())
        out = DOWNLOADS_DIR / f"{file_id}.pdf"
        with open(out, "wb") as fout:
            writer.write(fout)

        _download_names[file_id] = "combined.pdf"
        logger.info(f"Combined {len(files)} files into {file_id}.pdf")
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

    content = await _read_file_safe(file)
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
            # ponytail: blocking subprocess in async endpoint; fine for single-user local/Render tool
            result = subprocess.run(cmd, capture_output=True, timeout=120, check=False)
        except FileNotFoundError:
            raise HTTPException(
                500,
                detail="Ghostscript is not installed on this server. Please contact support or install Ghostscript.",
            )
        except subprocess.TimeoutExpired:
            raise HTTPException(500, detail="Compression timed out after 120 seconds")

        if result.returncode != 0:
            stderr = result.stderr.decode(errors="replace")[:300]
            logger.error(f"Ghostscript error: {stderr}")
            # Fall back to original file if Ghostscript fails or is not available
            shutil.copy2(temp_in, out)
            compressed_size = original_size
        else:
            compressed_size = out.stat().st_size

        # If compressed is larger or same, return the original
        if compressed_size >= original_size:
            shutil.copy2(temp_in, out)
            compressed_size = original_size

        _download_names[file_id] = "compressed.pdf"
        logger.info(f"Compressed {file.filename} -> {compressed_size} bytes (original: {original_size})")
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


# ──── Serve frontend & SPA fallback ────

@app.get("/")
async def root(request: Request):
    if "text/html" in request.headers.get("accept", ""):
        index_path = FRONTEND_DIR / "index.html"
        if index_path.exists():
            return FileResponse(str(index_path))
    return {"status": "ok", "service": "PDF Tool"}


@app.get("/{path_name:path}")
async def catch_all(request: Request, path_name: str):
    if path_name.startswith("api/"):
        raise HTTPException(status_code=404, detail="Not Found")
    file_path = FRONTEND_DIR / path_name
    if file_path.is_file():
        return FileResponse(str(file_path))
    index_path = FRONTEND_DIR / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    raise HTTPException(status_code=404, detail="Not Found")

