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

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Statische Dateien für Einzel-Downloads (Wichtig für dein neues Feature!)
os.makedirs("temp_pdfs", exist_ok=True)
app.mount("/download_single", StaticFiles(directory="temp_pdfs"), name="temp_pdfs")

status_db = {
    "is_running": False,
    "progress": 0,
    "total": 0,
    "current_item": "",
    "completed_files": [],
    "zip_ready": False
}

def get_pdf_config():
    # Wir suchen jetzt überall nach dem Drucker, damit der OSError verschwindet
    paths = ['/usr/bin/wkhtmltopdf', '/usr/local/bin/wkhtmltopdf', '/app/.nix-profile/bin/wkhtmltopdf']
    for p in paths:
        if os.path.exists(p):
            return pdfkit.configuration(wkhtmltopdf=p)
    return None

@app.get("/status")
async def get_status():
    return status_db

@app.post("/analyze")
async def analyze(data: dict):
    url = data.get("url")
    try:
        res = requests.get(url, timeout=10)
        soup = BeautifulSoup(res.text, 'html.parser')
        links = [requests.compat.urljoin(url, a.get('href')) for a in soup.find_all('a', href=True) 
                 if not any(x in a.get('href').lower() for x in ['#', 'mailto', 'javascript'])]
        status_db["total"] = len(links)
        return {"count": len(links), "links": links}
    except Exception as e:
        return {"error": str(e)}

@app.post("/generate")
async def generate(data: dict, background_tasks: BackgroundTasks):
    links = data.get("links", [])
    # CRITICAL FIX: Status SOFORT auf True setzen
    status_db["is_running"] = True
    status_db["progress"] = 0
    status_db["total"] = len(links)
    status_db["completed_files"] = []
    status_db["zip_ready"] = False
    
    def worker():
        try:
            config = get_pdf_config()
            if not config:
                print("FEHLER: wkhtmltopdf nicht gefunden!")
                return # Hier bricht er ab, wenn wkhtmltopdf fehlt

            if os.path.exists("temp_pdfs"): shutil.rmtree("temp_pdfs")
            os.makedirs("temp_pdfs", exist_ok=True)
            
            with zipfile.ZipFile("export.zip", "w") as z:
                for i, link in enumerate(links):
                    status_db["current_item"] = link
                    fname = f"temp_pdfs/doc_{i}.pdf"
                    try:
                        pdfkit.from_url(link, fname, configuration=config)
                        z.write(fname, os.path.basename(fname))
                        # Speichere Link für Einzel-Download
                        status_db["completed_files"].append({
                            "url": f"https://web-production-a7d6d.up.railway.app/download_single/doc_{i}.pdf",
                            "original": link
                        })
                    except Exception as e:
                        print(f"Fehler bei {link}: {e}")
                    
                    status_db["progress"] = i + 1
            
            time.sleep(2) # Zeit zum Versiegeln der ZIP
            status_db["zip_ready"] = True
        finally:
            status_db["is_running"] = False

    background_tasks.add_task(worker)
    return {"status": "started"}

@app.get("/download")
async def download():
    return FileResponse("export.zip", filename="gesetzessammlung.zip")
