import os
import asyncio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from playwright.async_api import async_playwright
import zipfile

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.post("/generate")
async def generate_pdfs(data: dict):
    links = data.get("links", [])
    os.makedirs("pdfs", exist_ok=True)
    
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_context()
        
        for i, link in enumerate(links[:10]): # Erstmal Test mit 10 Stück!
            try:
                new_page = await page.new_page()
                await new_page.goto(link, timeout=60000)
                await new_page.pdf(path=f"pdfs/seite_{i}.pdf")
                await new_page.close()
            except:
                continue
        
        await browser.close()

    # Alles in eine ZIP packen
    with zipfile.ZipFile("laws.zip", "w") as z:
        for f in os.listdir("pdfs"):
            z.write(f"pdfs/{f}", f)
            
    return {"download_url": "DEINE_URL/download"}
