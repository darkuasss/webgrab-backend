import os
import zipfile
import requests
from fastapi import FastAPI, BackgroundTasks
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from bs4 import BeautifulSoup
import pdfkit
import shutil
import time
import shutil

app = FastAPI()

# CORS FIX
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Statische Dateien für Einzel-Downloads freigeben
os.makedirs("temp_pdfs", exist_ok=True)
app.mount("/download_single", StaticFiles(directory="temp_pdfs"), name="temp_pdfs")
# ... deine anderen Imports ...

# AUTOMATISCHE PFAD-FINDER LOGIK
# shutil.which sucht im gesamten System-PATH nach dem Programm
wk_path = shutil.which("wkhtmltopdf")

try:
    if wk_path:
        # Er hat ihn automatisch gefunden (egal ob /usr/bin oder /nix/store/...)
        PDF_CONFIG = pdfkit.configuration(wkhtmltopdf=wk_path)
    else:
        # Letzter Versuch: Falls shutil versagt, lassen wir pdfkit suchen
        PDF_CONFIG = pdfkit.configuration()
except Exception as e:
    print(f"Warnung: PDF-Drucker konnte nicht initialisiert werden: {e}")
    PDF_CONFIG = None
# PDF-PFAD FINDER (Verhindert den Absturz aus image_e9f3dd.png)
def get_pdf_config():
    paths = ['/usr/bin/wkhtmltopdf', '/usr/local/bin/wkhtmltopdf']
    for p in paths:
        if os.path.exists(p):
            return pdfkit.configuration(wkhtmltopdf=p)
    return pdfkit.configuration() # Fallback

status_db = {
    "is_running": False,
    "progress": 0,
    "total": 0,
    "current_item": "",
    "completed_files": [],
    "zip_ready": False
}

@app.get("/status")
async def get_status():
    return status_db

@app.post("/analyze")
async def analyze(data: dict):
    url = data.get("url")
    res = requests.get(url, timeout=10)
    soup = BeautifulSoup(res.text, 'html.parser')
    links = [requests.compat.urljoin(url, a.get('href')) for a in soup.find_all('a', href=True) 
             if not any(x in a.get('href').lower() for x in ['#', 'mailto', 'javascript'])]
    status_db["total"] = len(links)
    return {"count": len(links), "links": links}

@app.post("/generate")
async def generate(data: dict, background_tasks: BackgroundTasks):
    links = data.get("links", [])
    status_db.update({"is_running": True, "progress": 0, "total": len(links), "completed_files": [], "zip_ready": False})
    
    def process():
        try:
            if os.path.exists("temp_pdfs"): shutil.rmtree("temp_pdfs")
            os.makedirs("temp_pdfs", exist_ok=True)
            config = get_pdf_config()
            
            with zipfile.ZipFile("export.zip", "w") as z:
                for i, link in enumerate(links):
                    status_db["current_item"] = link
                    fname = f"temp_pdfs/doc_{i}.pdf"
                    try:
                        pdfkit.from_url(link, fname, configuration=config)
                        z.write(fname, os.path.basename(fname))
                        status_db["completed_files"].append({"name": os.path.basename(link), "url": f"/download_single/doc_{i}.pdf"})
                    except: pass
                    status_db["progress"] = i + 1
            
            time.sleep(2) # Kurze Pause zum Versiegeln der ZIP
            status_db["zip_ready"] = True
        finally:
            status_db["is_running"] = False

    background_tasks.add_task(process)
    return {"status": "started"}

@app.get("/download")
async def download():
    return FileResponse("export.zip", filename="webgrab_sammlung.zip")
