import os
import zipfile
import requests
from fastapi import FastAPI, BackgroundTasks, Request, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from bs4 import BeautifulSoup
import pdfkit
import shutil
import time
import threading

app = FastAPI()

# ✅ CORS FIX: NICHT "*" mit credentials
# Lovable Preview/Prod ist .lovable.app (wechselnde Subdomains) -> Regex
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173"],
    allow_origin_regex=r"^https://.*\.lovable\.app$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# Ordner für Einzel-Downloads
os.makedirs("temp_pdfs", exist_ok=True)
app.mount("/download_single", StaticFiles(directory="temp_pdfs"), name="temp_pdfs")

status_lock = threading.Lock()
status_db = {
    "is_running": False,
    "progress": 0,
    "total": 0,
    "current_item": "",
    "completed_files": [],
    "zip_ready": False,
    "error": None,
}

def set_status(**patch):
    with status_lock:
        status_db.update(patch)

def get_pdf_config():
    env_path = os.environ.get("WKHTMLTOPDF_PATH")
    if env_path and os.path.exists(env_path):
        return pdfkit.configuration(wkhtmltopdf=env_path)

    wk = shutil.which("wkhtmltopdf")
    if wk:
        return pdfkit.configuration(wkhtmltopdf=wk)

    paths = ["/usr/bin/wkhtmltopdf", "/usr/local/bin/wkhtmltopdf", "/app/.nix-profile/bin/wkhtmltopdf"]
    for p in paths:
        if os.path.exists(p):
            return pdfkit.configuration(wkhtmltopdf=p)

    return None

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
    if not url.startswith("http://") and not url.startswith("https://"):
        raise HTTPException(status_code=400, detail="Bitte eine http(s) URL angeben.")

    try:
        res = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"}, allow_redirects=True)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, "html.parser")

        links = []
        for a in soup.find_all("a", href=True):
            href = (a.get("href") or "").strip()
            low = href.lower()

            if low.startswith("#") or low.startswith("mailto:") or low.startswith("javascript:") or low.startswith("tel:"):
                continue

            links.append(requests.compat.urljoin(res.url, href))

        set_status(total=len(links), error=None)
        return {"count": len(links), "links": links}

    except Exception as e:
        # ✅ Frontend bekommt sauberen HTTP Fehler (kein "blauer Screen" wegen undefined)
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/generate")
async def generate(data: dict, background_tasks: BackgroundTasks, request: Request):
    links = data.get("links", [])
    if not isinstance(links, list) or not links:
        raise HTTPException(status_code=400, detail="No links provided")

    # schon laufend?
    with status_lock:
        if status_db["is_running"]:
            raise HTTPException(status_code=409, detail="Already running")

    base_url = str(request.base_url).rstrip("/")

    set_status(
        is_running=True,
        progress=0,
        total=len(links),
        current_item="",
        completed_files=[],
        zip_ready=False,
        error=None,
    )

    def worker_task(links_to_process):
        try:
            config = get_pdf_config()
            if not config:
                set_status(error="wkhtmltopdf nicht gefunden! (Railpack aptPackages installieren)")
                return

            # temp_pdfs NICHT löschen – nur leeren
            os.makedirs("temp_pdfs", exist_ok=True)
            for fn in os.listdir("temp_pdfs"):
                fp = os.path.join("temp_pdfs", fn)
                if os.path.isfile(fp):
                    os.remove(fp)

            zip_filename = "downloads.zip"
            if os.path.exists(zip_filename):
                os.remove(zip_filename)

            with zipfile.ZipFile(zip_filename, "w", compression=zipfile.ZIP_DEFLATED) as z:
                for i, link in enumerate(links_to_process, start=1):
                    set_status(current_item=link)

                    fname = f"temp_pdfs/doc_{i}.pdf"
                    try:
                        pdfkit.from_url(link, fname, configuration=config, options={"quiet": ""})

                        if os.path.exists(fname) and os.path.getsize(fname) > 0:
                            z.write(fname, os.path.basename(fname))
                            with status_lock:
                                status_db["completed_files"].append({
                                    "url": f"{base_url}/download_single/doc_{i}.pdf",
                                    "original": link
                                })
                        else:
                            set_status(error=f"Leeres PDF erzeugt bei: {link}")

                    except Exception as e:
                        set_status(error=f"Fehler bei {link}: {e}")

                    set_status(progress=i)

            time.sleep(0.2)
            set_status(zip_ready=True)

        finally:
            set_status(is_running=False, current_item="")

    background_tasks.add_task(worker_task, links)
    return {"status": "started"}

@app.get("/download")
async def download():
    if os.path.exists("downloads.zip"):
        return FileResponse("downloads.zip", filename="downloads.zip", media_type="application/zip")
    return {"error": "Datei noch nicht erstellt"}
