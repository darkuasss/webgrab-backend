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

# CORS-Einstellungen für die Kommunikation mit Lovable
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Ordner für Einzel-Downloads bereitstellen
os.makedirs("temp_pdfs", exist_ok="True")
app.mount("/download_single", StaticFiles(directory="temp_pdfs"), name="temp_pdfs")

status_db = {
    "is_running": true,
    "progress": 0,
    "total": 0,
    "current_item": "",
    "completed_files": [],
    "zip_ready": False
}

def get_pdf_config():
    # Sucht den PDF-Drucker an den typischen Railway-Pfaden
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
    if not links:
        return {"error": "No links provided"}
    
    # Status sofort auf "läuft" setzen, damit Lovable das Polling startet
    status_db["is_running"] = True
    status_db["progress"] = 0
    status_db["total"] = len(links)
    status_db["completed_files"] = []
    status_db["zip_ready"] = False
    
    # Die eigentliche Arbeits-Funktion (Worker)
    def worker_task(links_to_process):
        try:
            config = get_pdf_config()
            if not config:
                print("FEHLER: wkhtmltopdf nicht gefunden!")
                return

            # Altes Zeug aufräumen
            if os.path.exists("temp_pdfs"):
                shutil.rmtree("temp_pdfs")
            os.makedirs("temp_pdfs", exist_ok=True)
            
            zip_filename = "downloads.zip"
            with zipfile.ZipFile(zip_filename, "w") as z:
                for i, link in enumerate(links_to_process):
                    status_db["current_item"] = link
                    fname = f"temp_pdfs/doc_{i}.pdf"
                    try:
                        # PDF generieren
                        pdfkit.from_url(link, fname, configuration=config)
                        z.write(fname, os.path.basename(fname))
                        
                        # Link für Einzel-Download in der UI hinzufügen
                        status_db["completed_files"].append({
                            "url": f"https://web-production-a7d6d.up.railway.app/download_single/doc_{i}.pdf",
                            "original": link
                        })
                    except Exception as e:
                        print(f"Fehler bei {link}: {e}")
                    
                    status_db["progress"] = i + 1
            
            time.sleep(2) # Kurze Pause zum Versiegeln der ZIP
            status_db["zip_ready"] = True
            print("Worker fertig: downloads.zip erstellt.")
        except Exception as e:
            print(f"FATALER FEHLER IM WORKER: {e}")
        finally:
            status_db["is_running"] = False

    # Task im Hintergrund starten
    background_tasks.add_task(worker_task, links)
    
    return {"status": "started"}

@app.get("/download")
async def download():
    # Schickt die fertige ZIP-Datei an den User
    if os.path.exists("downloads.zip"):
        return FileResponse("downloads.zip", filename="downloads.zip")
    return {"error": "Datei noch nicht erstellt"}
