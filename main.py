import os
import shutil
import zipfile
import threading
import time
from urllib.parse import urljoin, urldefrag, urlparse

import requests
from bs4 import BeautifulSoup
import pdfkit

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware


app = FastAPI()

# CORS (für Lovable o.ä.)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

WORK_DIR = "work"
PDF_DIR = os.path.join(WORK_DIR, "pdfs")
ZIP_PATH = os.path.join(WORK_DIR, "downloads.zip")

os.makedirs(PDF_DIR, exist_ok=True)

status_lock = threading.Lock()
status_db = {
    "is_running": False,
    "progress": 0,
    "total": 0,
    "current_item": "",
    "completed_files": [],   # [{url, original, filename}]
    "zip_ready": False,
    "error": None,
    "base_url": "",
}


def set_status(**patch):
    with status_lock:
        status_db.update(patch)


def get_pdf_config():
    # 1) optional env
    env_path = os.environ.get("WKHTMLTOPDF_PATH")
    if env_path and os.path.exists(env_path):
        return pdfkit.configuration(wkhtmltopdf=env_path)

    # 2) PATH
    wk = shutil.which("wkhtmltopdf")
    if wk:
        return pdfkit.configuration(wkhtmltopdf=wk)

    # 3) bekannte Pfade
    for p in ("/usr/bin/wkhtmltopdf", "/usr/local/bin/wkhtmltopdf", "/app/.nix-profile/bin/wkhtmltopdf"):
        if os.path.exists(p):
            return pdfkit.configuration(wkhtmltopdf=p)

    return None


def normalize_url(u: str) -> str:
    u, _ = urldefrag(u)
    return u.strip()


def is_http(u: str) -> bool:
    try:
        return urlparse(u).scheme.lower() in ("http", "https")
    except Exception:
        return False


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/status")
async def get_status():
    with status_lock:
        return dict(status_db)


@app.post("/analyze")
async def analyze(data: dict):
    url = (data.get("url") or "").strip()
    same_domain_only = bool(data.get("same_domain_only", True))
    max_links = int(data.get("max_links", 300))

    if not url or not is_http(url):
        raise HTTPException(status_code=400, detail="Invalid url")

    try:
        res = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        res.raise_for_status()
        soup = BeautifulSoup(res.text, "html.parser")

        base_netloc = urlparse(url).netloc.lower()
        links = []
        seen = set()

        for a in soup.find_all("a", href=True):
            href = (a.get("href") or "").strip()
            if not href:
                continue

            low = href.lower()
            if low.startswith("#") or low.startswith("mailto:") or low.startswith("javascript:") or low.startswith("tel:"):
                continue

            abs_url = normalize_url(urljoin(url, href))
            if not is_http(abs_url):
                continue

            if same_domain_only and urlparse(abs_url).netloc.lower() != base_netloc:
                continue

            if abs_url in seen:
                continue

            seen.add(abs_url)
            links.append(abs_url)

            if len(links) >= max_links:
                break

        set_status(total=len(links))
        return {"count": len(links), "links": links}

    except Exception as e:
        return {"error": str(e)}


@app.post("/generate")
async def generate(data: dict, request: Request):
    links = data.get("links", [])
    if not isinstance(links, list) or not links:
        raise HTTPException(status_code=400, detail="No links provided")

    # nur gültige http(s)-links
    clean_links = [normalize_url(x) for x in links if isinstance(x, str) and is_http(x)]
    if not clean_links:
        raise HTTPException(status_code=400, detail="No valid http(s) links provided")

    with status_lock:
        if status_db["is_running"]:
            raise HTTPException(status_code=409, detail="Generation already running")

    base_url = str(request.base_url)  # z.B. https://xyz.up.railway.app/

    # Status reset
    set_status(
        is_running=True,
        progress=0,
        total=len(clean_links),
        current_item="",
        completed_files=[],
        zip_ready=False,
        error=None,
        base_url=base_url,
    )

    # Thread starten (damit /status Polling weiter funktioniert)
    t = threading.Thread(target=worker_task, args=(clean_links,), daemon=True)
    t.start()

    return {"status": "started"}


def worker_task(links_to_process: list[str]):
    try:
        config = get_pdf_config()
        if not config:
            set_status(
                error="wkhtmltopdf not found. Install it via Railpack Apt packages (RAILPACK_DEPLOY_APT_PACKAGES).",
                is_running=False,
            )
            return

        # Arbeitsordner frisch machen
        if os.path.exists(WORK_DIR):
            shutil.rmtree(WORK_DIR)
        os.makedirs(PDF_DIR, exist_ok=True)

        options = {
            "quiet": "",
            "print-media-type": "",
        }

        with zipfile.ZipFile(ZIP_PATH, "w", compression=zipfile.ZIP_DEFLATED) as z:
            for i, link in enumerate(links_to_process, start=1):
                set_status(current_item=link)

                filename = f"doc_{i:04d}.pdf"
                pdf_path = os.path.join(PDF_DIR, filename)

                try:
                    pdfkit.from_url(link, pdf_path, configuration=config, options=options)

                    if os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 0:
                        z.write(pdf_path, arcname=filename)

                        with status_lock:
                            status_db["completed_files"].append({
                                "url": f"{status_db['base_url']}download_single/{filename}",
                                "original": link,
                                "filename": filename,
                            })
                    else:
                        # leerer output
                        pass

                except Exception:
                    # einzel-fehler ignorieren, weiter machen
                    pass

                set_status(progress=i)

        # ZIP ist da
        set_status(zip_ready=True)

    except Exception as e:
        set_status(error=str(e))
    finally:
        set_status(is_running=False, current_item="")


@app.get("/download")
async def download():
    if os.path.exists(ZIP_PATH):
        return FileResponse(ZIP_PATH, filename="downloads.zip", media_type="application/zip")
    return {"error": "Datei noch nicht erstellt"}


@app.get("/download_single/{filename}")
async def download_single(filename: str):
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only .pdf allowed")

    path = os.path.join(PDF_DIR, filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(path, filename=filename, media_type="application/pdf")
