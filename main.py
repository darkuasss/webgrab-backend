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

# Statische Dateien für Einzel-Downloads
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
    # Wir probieren alle bekannten Railway/Nixpacks Pfade durch
    paths = [
        '/usr/bin/wkhtmltopdf', 
        '/usr/local/bin/wkhtmltopdf',
        '/app/.nix-profile/bin/wkhtmltopdf'
    ]
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
    res = requests.get(url, timeout=10)
    soup = BeautifulSoup(res.text, 'html.parser')
    links = [requests.compat.urljoin(url, a.get('href')) for a in soup.find_all('a', href=True) 
             if not any(x in a.get('href').lower() for x in ['#', 'mailto', 'javascript'])]
    status_db["total"] = len(links)
    return {"count": len(links), "links": links}

@app.post("/generate")
async def generate(data: dict, background_tasks: BackgroundTasks):
    links = data.get("links", [])
    # WICHTIG: Sofort auf True setzen!
    status_db.update({"is_running": True, "progress": 0, "total": len(links), "completed_files": [], "zip_ready": False})
    
    def process_logic():
        try:
            config = get_pdf_config()
            if not config:
                print("FEHLER: wkhtmltopdf wurde nirgends gefunden!")
                status_db["is_running"] = False
                return

            if os.path.exists("temp_pdfs"): shutil.rmtree("temp_pdfs")
            os.makedirs("temp_pdfs", exist_ok=True)
            
            zip_path = "export.zip"
            with zipfile.ZipFile(zip_path, "w") as z:
                for i, link in enumerate(links):
                    status_db["current_item"] = link
                    fname = f"temp_pdfs/doc_{i}.pdf"
                    try:
                        pdfkit.from_url(link, fname, configuration=config)
                        z.write(fname, os.path.basename(fname))
                        status_db["completed_files"].append({"name": f"Gesetz {i}", "url": f"/download_single/doc_{i}.pdf"})
                    except Exception as e:
                        print(f"Fehler bei Link {i}: {e}")
                    
                    status_db["progress"] = i + 1
            
            time.sleep(1)
            status_db["zip_ready"] = True
        finally:
            status_db["is_running"] = False

    background_tasks.add_task(process_logic)
    return {"status": "started"}

@app.get("/download")
async def download():
    return FileResponse("export.zip", filename="webgrab_archiv.zip")
