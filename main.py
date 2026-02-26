import os
import zipfile
import requests
from fastapi import FastAPI, BackgroundTasks
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from bs4 import BeautifulSoup
import pdfkit
import shutil

app = FastAPI()

# 1. CORS FIX: Das ist der Schlüssel, damit der "Fetch" Fehler verschwindet!
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 2. PDF-DRUCKER FINDEN (Railway Fix)
try:
    # Wir lassen pdfkit automatisch nach wkhtmltopdf suchen
    PDF_CONFIG = pdfkit.configuration()
except:
    # Fallback Pfad falls er auf Railway woanders liegt
    PDF_CONFIG = pdfkit.configuration(wkhtmltopdf='/usr/bin/wkhtmltopdf')

status_db = {
    "is_running": False,
    "progress": 0,
    "total": 0,
    "current_item": "",
    "completed_files": []
}

@app.get("/status")
async def get_status():
    return status_db

@app.post("/analyze")
async def analyze(data: dict):
    url = data.get("url")
    try:
        res = requests.get(url, timeout=15)
        soup = BeautifulSoup(res.text, 'html.parser')
        # Findet alle relevanten Links und filtert Müll raus
        links = [requests.compat.urljoin(url, a.get('href')) for a in soup.find_all('a', href=True) 
                 if not any(x in a.get('href').lower() for x in ['#', 'mailto', 'javascript', 'impressum'])]
        
        status_db["total"] = len(links)
        status_db["completed_files"] = []
        return {"count": len(links), "links": links}
    except Exception as e:
        return {"error": str(e)}

@app.post("/generate")
async def generate(data: dict, background_tasks: BackgroundTasks):
    links = data.get("links", [])
    if not links:
        return {"error": "No links provided"}
    
    # Sofortiger Status-Update für Lovable
    status_db["is_running"] = True
    status_db["progress"] = 0
    status_db["total"] = len(links)
    status_db["completed_files"] = []
    
    def background_work():
        try:
            # Ordner vorbereiten
            if os.path.exists("temp_pdfs"):
                shutil.rmtree("temp_pdfs")
            os.makedirs("temp_pdfs", exist_ok=True)
            
            zip_path = "export.zip"
            with zipfile.ZipFile(zip_path, "w") as z:
                for i, link in enumerate(links):
                    status_db["current_item"] = link
                    fname = f"temp_pdfs/doc_{i}.pdf"
                    try:
                        # Hier passiert das eigentliche PDF-Erstellen
                        pdfkit.from_url(link, fname, configuration=PDF_CONFIG)
                        z.write(fname, os.path.basename(fname))
                        status_db["completed_files"].append(link)
                    except:
                        continue
                    
                    status_db["progress"] = i + 1
        finally:
            status_db["is_running"] = False

    background_tasks.add_task(background_work)
    return {"status": "started"}

@app.get("/download")
async def download():
    if os.path.exists("export.zip"):
        return FileResponse("export.zip", filename="webgrab_sammlung.zip")
    return {"error": "Datei noch nicht bereit"}
