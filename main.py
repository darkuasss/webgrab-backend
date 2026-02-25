from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import requests
from bs4 import BeautifulSoup

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def home():
    return {"message": "Motor läuft!"}

@app.post("/analyze")
async def analyze(data: dict):
    url = data.get("url")
    # Hier passiert das echte Grabbing
    res = requests.get(url)
    soup = BeautifulSoup(res.text, 'html.parser')
    links = [a.get('href') for a in soup.find_all('a', href=True)]
    return {"count": len(links)}
