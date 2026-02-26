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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Ordner für Einzel-Downloads
os.makedirs("temp_pdfs", exist_ok=True)
app.mount("/download_single", StaticFiles(directory="temp_pdfs"), name="temp_pdfs")

status_lock = threading.Lock()
status_db = {
    "is_running": False,   # ✅ Python bool
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

@app.get("/status")
async def get_status():
    with status_lock:
        return dict(status_db)

@app.post("/analyze")
async def analyze(data: dict):
    url = (data.get("url") or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="No url provided")

    try:
        res = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        res.raise_for_status()
        soup = BeautifulSoup(res.text, "html.parser")

        links = []
        for a in soup.find_all("a", href=True):
            href = a.get("href") or ""
            low = href.lower()
            if any(x in low for x in ["#", "mailto:", "javascript:"]):
                continue
            links.append(requests.compat.urljoin(url, href))

        set_status(total=len(links))
        return {"count": len(links), "links": links}
    except Exception as e:
        return {"error": str(e)}

@app.post("/generate")
async def generate(data: dict, background_tasks: BackgroundTasks, request: Request):
    links = data.get("links", [])
    if not links:
        raise HTTPException(status_code=400, detail="No links provided")

    # schon laufend?
    with status_lock:
        if status_db["is_running"]:
            raise HTTPException(status_code=409, detail="Already running")

    base_url = str(request.base_url).rstrip("/")  # ✅ dynamisch statt hardcoded

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
                set_status(error="wkhtmltopdf nicht gefunden! (Railway/Railpack apt packages installieren)")
                return

            # temp_pdfs NICHT löschen – nur leeren
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
                        z.write(fname, os.path.basename(fname))

                        with status_lock:
                            status_db["completed_files"].append({
                                "url": f"{base_url}/download_single/doc_{i}.pdf",
                                "original": link
                            })
                    except Exception as e:
                        # weiter machen, aber Fehler merken
                        set_status(error=f"Fehler bei {link}: {e}")

                    set_status(progress=i)

            time.sleep(0.5)
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
