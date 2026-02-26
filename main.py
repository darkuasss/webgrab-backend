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

# EINMALIGE, RICHTIGE CORS-EINSTELLUNGEN
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# PDF-PFAD LOGIK FÜR RAILWAY
# Er sucht erst dort, wo Railway Pakete installiert, dann im Standard-Pfad
possible_paths = ['/usr/bin/wkhtmltopdf', '/usr/local/bin/wkhtmltopdf', '/nix/store']
wk_path = None

for path in possible_paths:
    if os.path.exists(path):
        wk_path = path
        break

# Falls er nichts findet, lassen wir pdfkit einfach so suchen
try:
    if wk_path:
        PDF_CONFIG = pdfkit.configuration(wkhtmltopdf=wk_path)
    else:
        PDF_CONFIG = pdfkit.configuration()
except:
    PDF_CONFIG = None

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
        res = requests.get(url, timeout=10)
        soup = BeautifulSoup(res.text, 'html.parser')
        # Filtert Schrott-Links aus
        links = [requests.compat.urljoin(url, a.get('href')) for a in soup.find_all('a', href=True) 
                 if not any(x in a.get('href').lower() for x in ['#', 'mailto', 'impressum', 'datenschutz', 'javascript'])]
        
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
    
    status_db["is_running"] = True
    status_db["progress"] = 0
    status_db["total"] = len(links)
    status_db["completed_files"] = []
    
    def process_everything():
        try:
            # Ordner leeren falls er existiert
            if os.path.exists("temp_pdfs"):
                shutil.rmtree("temp_pdfs")
            os.makedirs("temp_pdfs", exist_ok=True)
            
            zip_path = "export.zip"
            with zipfile.ZipFile(zip_path, "w") as z:
                for i, link in enumerate(links):
                    status_db["current_item"] = link
                    fname = f"temp_pdfs/dokument_{i}.pdf"
                    try:
                        # Web-Inhalt zu PDF machen
                        pdfkit.from_url(link, fname, configuration=PDF_CONFIG)
                        z.write(fname, os.path.basename(fname))
                        status_db["completed_files"].append(link)
                    except Exception as e:
                        print(f"Fehler bei {link}: {e}")
                    
                    status_db["progress"] = i + 1
        finally:
            status_db["is_running"] = False

    background_tasks.add_task(process_everything)
    return {"status": "started"}

@app.get("/download")
async def download():
    if os.path.exists("export.zip"):
        return FileResponse("export.zip", filename="gesetzessammlung.zip")
    return {"error": "File not found. Please generate first."}
