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
    
    links = []
    # Die "Verbotene Liste" - diese Wörter ignorieren wir
    blacklist = ["impressum", "datenschutz", "kontakt", "suche", "newsletter"]
    
    for a in soup.find_all('a', href=True):
        href = a.get('href')
        text = a.text.lower()
        
        # Check 1: Ist der Link in der Blacklist?
        if any(word in href.lower() or word in text for word in blacklist):
            continue
            
        # Check 2: Ist es eine PDF oder ein Unterlink (Gesetz/Verordnung)?
        if href.endswith('.pdf') or "BJNR" in href or "Teilliste" in href or len(text) > 5:
            full_url = requests.compat.urljoin(url, href)
            links.append(full_url)
    
    # Entferne Duplikate
    unique_links = list(set(links))
    return {"count": len(unique_links), "links": unique_links}
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
