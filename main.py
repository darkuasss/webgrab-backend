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

# DAS IST DER LIVE-STATUS
status_db = {
    "is_running": False,
    "progress": 0,
    "total": 0,
    "current_item": "",
    "completed_files": []
}

@app.post("/analyze")
async def analyze(data: dict):
    url = data.get("url")
    res = requests.get(url)
    soup = BeautifulSoup(res.text, 'html.parser')
    links = [requests.compat.urljoin(url, a.get('href')) for a in soup.find_all('a', href=True) if not any(x in a.get('href') for x in ['#', 'mailto'])]
    status_db["total"] = len(links)
    status_db["completed_files"] = []
    return {"count": len(links), "links": links}

@app.post("/generate")
async def generate(data: dict, background_tasks: BackgroundTasks):
    links = data.get("links", [])
    if not links:
        return {"error": "No links provided"}
    
    # SOFORT auf True setzen, damit Lovable den Fortschritt sieht!
    status_db["is_running"] = True
    status_db["progress"] = 0
    status_db["total"] = len(links)
    status_db["completed_files"] = []
    
    def process_everything():
        try:
            os.makedirs("temp_pdfs", exist_ok=True)
            with zipfile.ZipFile("export.zip", "w") as z:
                for i, link in enumerate(links):
                    # Hier wird gearbeitet
                    status_db["current_item"] = link
                    # ... (PDF Logik) ...
                    status_db["progress"] = i + 1
                    status_db["completed_files"].append(link)
        finally:
            status_db["is_running"] = False # Erst ganz am Ende ausmachen

    background_tasks.add_task(process_everything)
    return {"status": "started"}

@app.get("/status")
async def get_status():
    return status_db

@app.get("/download")
async def download():
    return FileResponse("export.zip")
