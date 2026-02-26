import os
import zipfile
import requests
from fastapi import FastAPI, BackgroundTasks
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from bs4 import BeautifulSoup
import pdfkit

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# Konfiguration für Railway (wkhtmltopdf Pfad)
# Wir versuchen den Standardpfad von Linux zu nutzen
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
    res = requests.get(url)
    soup = BeautifulSoup(res.text, 'html.parser')
    links = [requests.compat.urljoin(url, a.get('href')) for a in soup.find_all('a', href=True) if not any(x in a.get('href').lower() for x in ['#', 'mailto', 'impressum', 'datenschutz'])]
    status_db["total"] = len(links)
    status_db["completed_files"] = []
    return {"count": len(links), "links": links}

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
            os.makedirs("temp_pdfs", exist_ok=True)
            zip_path = "export.zip"
            with zipfile.ZipFile(zip_path, "w") as z:
                for i, link in enumerate(links):
                    status_db["current_item"] = link
                    fname = f"temp_pdfs/dokument_{i}.pdf"
                    try:
                        # HIER IST DIE ECHTE MAGIE:
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
    return FileResponse("export.zip", filename="gesetzessammlung.zip")
