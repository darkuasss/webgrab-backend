import os
import zipfile
from fastapi import FastAPI, BackgroundTasks
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
import requests
from bs4 import BeautifulSoup
import pdfkit

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# Speicher für den Status
jobs = {}

@app.post("/analyze")
async def analyze(data: dict):
    url = data.get("url")
    res = requests.get(url)
    soup = BeautifulSoup(res.text, 'html.parser')
    
    # Sucht jetzt nach ALLES, was auf .pdf endet ODER ein Gesetz sein könnte
    links = []
    for a in soup.find_all('a', href=True):
        href = a.get('href')
        # Wir nehmen PDF-Links ODER Links, die wie Gesetze aussehen
        if href.endswith('.pdf') or "Teilliste" in href or "BJNR" in href:
            # Baue die volle URL zusammen
            full_url = requests.compat.urljoin(url, href)
            links.append(full_url)
    
    return {"count": len(links), "links": list(set(links))} # set() entfernt Doppelte
@app.post("/generate")
async def generate(data: dict, background_tasks: BackgroundTasks):
    links = data.get("links", [])
    os.makedirs("output", exist_ok=True)
    
    def create_pdfs():
        with zipfile.ZipFile("webgrab-pdfs.zip", "w") as z:
            for i, link in enumerate(links[:20]): # Test-Limit auf 20!
                filename = f"output/gesetz_{i}.pdf"
                try:
                    pdfkit.from_url(link, filename)
                    z.write(filename, os.path.basename(filename))
                except: continue
    
    background_tasks.add_task(create_pdfs)
    return {"message": "Generation started"}

@app.get("/download")
async def download():
    return FileResponse("webgrab-pdfs.zip", media_type="application/zip", filename="webgrab-pdfs.zip")
