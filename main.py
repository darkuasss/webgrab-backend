import os
import zipfile
from fastapi import FastAPI, BackgroundTasks, Request, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from bs4 import BeautifulSoup
import pdfkit
import shutil
import time
import threading
import urllib.parse
from playwright.async_api import async_playwright # NEU: Playwright Import

app = FastAPI()

# CORS-Einstellungen für Lovable
from fastapi.middleware.cors import CORSMiddleware

# ... (nach app = FastAPI())

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173", # Lokales Testing
        "https://*.lovableproject.com", 
        "https://*.lovable.app",
        "*" # Der "Brecheisen"-Modus: Erlaubt JEDE Quelle
    ],
    allow_credentials=True,
    allow_methods=["*"], # Erlaubt GET, POST, OPTIONS etc.
    allow_headers=["*"], # Erlaubt alle Header (wichtig für Preflight!)
)

os.makedirs("temp_pdfs", exist_ok=True)
app.mount("/download_single", StaticFiles(directory="temp_pdfs"), name="temp_pdfs")

status_lock = threading.Lock()
status_db = {
    "is_running": False, "progress": 0, "total": 0, "current_item": "",
    "completed_files": [], "zip_ready": False, "error": None,
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

# 🔥 HIER IST DIE MAGIE: Der neue Playwright-Analyzer 🔥
@app.post("/analyze")
async def analyze(data: dict):
    url = (data.get("url") or "").strip()
    if not url.startswith("http://") and not url.startswith("https://"):
        raise HTTPException(status_code=400, detail="Bitte eine gültige http(s) URL angeben.")

    try:
        # Starte den unsichtbaren Browser
        async with async_playwright() as p:
            browser = await p.chromium.launch(
    headless=True,
    args=["--no-sandbox", "--disable-dev-shm-usage"]
)
            # Simuliere einen echten Windows/Chrome-Nutzer
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                viewport={"width": 1920, "height": 1080},
                java_script_enabled=True
            )
            page = await context.new_page()
            
            # Lade die Seite und warte, bis das Netzwerk für 500ms ruhig ist (JS ist durch)
            await page.goto(url, wait_until="networkidle", timeout=45000)
            
            # Puffer: Warte extra 2 Sekunden, falls späte Skripte noch nachladen
            await page.wait_for_timeout(2000)
            
            # Ziehe den komplett gerenderten Quellcode raus
            html_content = await page.content()
            await browser.close()

        # Jetzt analysieren wir das FERTIGE HTML mit BeautifulSoup
        soup = BeautifulSoup(html_content, "html.parser")
        links = []
        for a in soup.find_all("a", href=True):
            href = (a.get("href") or "").strip()
            low = href.lower()

            # Unnötiges Zeug filtern
            if low.startswith("#") or low.startswith("mailto:") or low.startswith("javascript:") or low.startswith("tel:"):
                continue

            # Absolute URL zusammenbauen
            absolute_link = urllib.parse.urljoin(url, href)
            links.append(absolute_link)

        # Duplikate entfernen
        unique_links = list(set(links))

        set_status(total=len(unique_links), error=None)
        return {"count": len(unique_links), "links": unique_links}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Fehler beim Scraping mit Playwright: {str(e)}")

# ... (Der /generate und /download Endpunkt bleiben exakt gleich wie in deiner vorherigen funktionierenden Version) ...
@app.post("/generate")
async def generate(data: dict, background_tasks: BackgroundTasks, request: Request):
    links = data.get("links", [])
    if not isinstance(links, list) or not links:
        raise HTTPException(status_code=400, detail="No links provided")

    with status_lock:
        if status_db["is_running"]:
            raise HTTPException(status_code=409, detail="Already running")

    base_url = str(request.base_url).rstrip("/")

    set_status(
        is_running=True, progress=0, total=len(links), current_item="",
        completed_files=[], zip_ready=False, error=None,
    )

    def worker_task(links_to_process):
        try:
            config = get_pdf_config()
            if not config:
                set_status(error="wkhtmltopdf nicht gefunden!")
                return

            os.makedirs("temp_pdfs", exist_ok=True)
            for fn in os.listdir("temp_pdfs"):
                fp = os.path.join("temp_pdfs", fn)
                if os.path.isfile(fp): os.remove(fp)

            zip_filename = "downloads.zip"
            if os.path.exists(zip_filename): os.remove(zip_filename)

            with zipfile.ZipFile(zip_filename, "w", compression=zipfile.ZIP_DEFLATED) as z:
                for i, link in enumerate(links_to_process, start=1):
                    set_status(current_item=link)
                    fname = f"temp_pdfs/doc_{i}.pdf"
                    try:
                        options = {"quiet": "", "no-stop-slow-scripts": "", "javascript-delay": "1000"}
                        pdfkit.from_url(link, fname, configuration=config, options=options)
                        if os.path.exists(fname) and os.path.getsize(fname) > 0:
                            z.write(fname, os.path.basename(fname))
                            with status_lock:
                                status_db["completed_files"].append({
                                    "url": f"{base_url}/download_single/doc_{i}.pdf",
                                    "original": link
                                })
                        else:
                            set_status(error=f"Leeres PDF bei: {link}")
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
