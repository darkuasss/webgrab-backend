import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import requests
from bs4 import BeautifulSoup

app = FastAPI()

# WICHTIG: Erlaubt Lovable den Zugriff
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def read_root():
    return {"status": "Backend läuft, Bruder!"}

@app.post("/analyze")
async def analyze(data: dict):
    url = data.get("url")
    try:
        response = requests.get(url, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')
        # Sucht alle blauen Links (Anker-Tags)
        links = [a.get('href') for a in soup.find_all('a', href=True)]
        # Filtert z.B. nur die relevanten Gesetz-Links (BJNR...)
        valid_links = [l for l in links if "BJNR" in l]
        return {"count": len(valid_links), "links": valid_links}
    except Exception as e:
        return {"error": str(e)}