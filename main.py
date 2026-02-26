import os
import re
import time
import zipfile
import shutil
import threading
from urllib.parse import urljoin, urldefrag

import requests
from bs4 import BeautifulSoup
from fastapi import FastAPI, BackgroundTasks, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Ordner für Einzel-Downloads
os.makedirs("temp_pdfs", exist_ok=True)
app.mount("/download_single", StaticFiles(directory="temp_pdfs"), name="temp_pdfs")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,application/pdf,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

DEFAULT_COOKIES = {
    "consent": "true",
    "cookies-accepted": "yes",
    "allow-all": "true",
}

status_lock = threading.Lock()
status_db = {
    "is_running": False,
    "progress": 0,
    "total": 0,
    "current_item": "",
    "completed_files": [],
    "zip_ready": False,
    "error": None,
    "pdf_found": 0,
}

ZIP_FILENAME = "downloads.zip"


def set_status(**patch):
    with status_lock:
        status_db.update(patch)


def normalize_url(u: str, base: str) -> str:
    u = (u or "").strip()
    if not u:
        return ""
    u = urljoin(base, u)
    u, _ = urldefrag(u)
    return u


def looks_like_pdf_url(u: str) -> bool:
    u_low = u.lower()
    return (
        u_low.endswith(".pdf") or
        ".pdf?" in u_low or
        "format=pdf" in u_low or
        "type=pdf" in u_low or
        "download=pdf" in u_low
    )


def is_pdf_by_probe(session: requests.Session, url: str, timeout: int = 15) -> tuple[bool, str, str]:
    """
    Returns: (is_pdf, final_url, reason)
    Verifikation: HEAD -> Content-Type; falls unsicher -> GET stream -> check %PDF-
    """
    try:
        # 1) HEAD
        try:
            r = session.head(url, headers=HEADERS, allow_redirects=True, timeout=timeout)
            ct = (r.headers.get("Content-Type") or "").lower()
            cd = (r.headers.get("Content-Disposition") or "").lower()

            if "application/pdf" in ct:
                return True, r.url, "head content-type=application/pdf"

            # manche Server liefern octet-stream, aber disposition verrät PDF
            if ("octet-stream" in ct or ct == "") and ("pdf" in cd or ".pdf" in cd):
                return True, r.url, "head octet-stream + content-disposition hints pdf"
        except Exception:
            pass

        # 2) GET probe (nur erste Bytes)
        r2 = session.get(url, headers=HEADERS, allow_redirects=True, timeout=timeout, stream=True)
        ct2 = (r2.headers.get("Content-Type") or "").lower()
        if "application/pdf" in ct2:
            return True, r2.url, "get content-type=application/pdf"

        # read first bytes
        chunk = next(r2.iter_content(chunk_size=16), b"")
        if chunk.startswith(b"%PDF-"):
            return True, r2.url, "get magic-bytes %PDF-"

        return False, r2.url, f"not pdf (ct={ct2 or 'n/a'})"
    except Exception as e:
        return False, url, f"probe error: {e}"


def extract_candidates(html: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    cand = set()

    # a href
    for a in soup.find_all("a", href=True):
        u = normalize_url(a.get("href"), base_url)
        if u:
            cand.add(u)

    # iframe/embed/object/link (pdf viewer embeds!)
    for tag, attr in [("iframe", "src"), ("embed", "src"), ("object", "data"), ("link", "href")]:
        for el in soup.find_all(tag):
            v = el.get(attr)
            u = normalize_url(v, base_url)
            if u:
                cand.add(u)

    # onclick="window.open('...pdf')"
    for el in soup.find_all(onclick=True):
        txt = el.get("onclick") or ""
        for m in re.findall(r"""['"]([^'"]+\.pdf[^'"]*)['"]""", txt, flags=re.IGNORECASE):
            u = normalize_url(m, base_url)
            if u:
                cand.add(u)

    # PDFs in Script-Text / Inline JSON
    #  - absolute URLs
    for m in re.findall(r"""https?://[^\s"'<>]+\.pdf(?:\?[^\s"'<>]*)?""", html, flags=re.IGNORECASE):
        cand.add(normalize_url(m, base_url))
    #  - relative URLs in quotes
    for m in re.findall(r"""['"]([^'"]+\.pdf(?:\?[^'"]*)?)['"]""", html, flags=re.IGNORECASE):
        u = normalize_url(m, base_url)
        if u:
            cand.add(u)

    # Filter: nur http(s)
    out = []
    for u in cand:
        if u.startswith("http://") or u.startswith("https://"):
            out.append(u)

    # Priorisiere "pdf-looking" URLs zuerst
    out.sort(key=lambda x: 0 if looks_like_pdf_url(x) else 1)
    return out


@app.get("/status")
def get_status():
    with status_lock:
        return dict(status_db)


@app.post("/analyze")
def analyze(data: dict):
    url = (data.get("url") or "").strip()
    if not url.startswith("http://") and not url.startswith("https://"):
        raise HTTPException(status_code=400, detail="Bitte eine http(s) URL angeben.")

    timeout = int(data.get("timeout", 15))
    max_checks = int(data.get("max_checks", 400))  # wie viele Kandidaten wirklich prüfen

    session = requests.Session()
    session.cookies.update(DEFAULT_COOKIES)

    # 1) Seite laden
    res = session.get(url, headers={**HEADERS, "Referer": "https://www.google.com/"},
                      timeout=timeout, allow_redirects=True)
    res.raise_for_status()

    # 2) Kandidaten extrahieren
    candidates = extract_candidates(res.text, res.url)

    # 3) Kandidaten als PDF verifizieren
    pdfs = []
    checked = 0

    for u in candidates:
        checked += 1
        if checked > max_checks:
            break

        ok, final_url, _reason = is_pdf_by_probe(session, u, timeout=timeout)
        if ok:
            if final_url not in pdfs:
                pdfs.append(final_url)

    set_status(total=len(pdfs), pdf_found=len(pdfs))

    # Lovable-kompatibel: count + links
    return {
        "count": len(pdfs),
        "links": pdfs,
        "checked_candidates": min(len(candidates), max_checks),
        "total_candidates": len(candidates),
        "base_page": res.url,
    }


@app.post("/generate")
def generate(data: dict, background_tasks: BackgroundTasks, request: Request):
    links = data.get("links", [])
    if not isinstance(links, list) or not links:
        raise HTTPException(status_code=400, detail="No links provided")

    # nur http(s)
    links = [x for x in links if isinstance(x, str) and (x.startswith("http://") or x.startswith("https://"))]
    if not links:
        raise HTTPException(status_code=400, detail="No valid http(s) links provided")

    base_url = str(request.base_url).rstrip("/")

    # Status reset
    set_status(
        is_running=True,
        progress=0,
        total=len(links),
        current_item="",
        completed_files=[],
        zip_ready=False,
        error=None,
    )

    def worker(pdf_urls: list[str]):
        session = requests.Session()
        session.cookies.update(DEFAULT_COOKIES)

        try:
            # temp_pdfs leeren (nicht löschen!)
            os.makedirs("temp_pdfs", exist_ok=True)
            for fn in os.listdir("temp_pdfs"):
                fp = os.path.join("temp_pdfs", fn)
                if os.path.isfile(fp):
                    os.remove(fp)

            if os.path.exists(ZIP_FILENAME):
                os.remove(ZIP_FILENAME)

            with zipfile.ZipFile(ZIP_FILENAME, "w", compression=zipfile.ZIP_DEFLATED) as z:
                for i, pdf_url in enumerate(pdf_urls, start=1):
                    set_status(current_item=pdf_url)

                    # Nochmal verifizieren & final url holen
                    ok, final_url, reason = is_pdf_by_probe(session, pdf_url, timeout=30)
                    if not ok:
                        set_status(error=f"Skip (kein PDF): {pdf_url} | {reason}")
                        set_status(progress=i)
                        continue

                    filename = f"file_{i:04d}.pdf"
                    out_path = os.path.join("temp_pdfs", filename)

                    try:
                        r = session.get(final_url, headers=HEADERS, stream=True, timeout=60)
                        r.raise_for_status()

                        # Header-Check
                        first = next(r.iter_content(chunk_size=16), b"")
                        if not first.startswith(b"%PDF-"):
                            # trotzdem speichern? nein -> skip
                            set_status(error=f"Skip (kein %PDF- Header): {final_url}")
                            set_status(progress=i)
                            continue

                        with open(out_path, "wb") as f:
                            f.write(first)
                            for chunk in r.iter_content(chunk_size=1024 * 64):
                                if chunk:
                                    f.write(chunk)

                        z.write(out_path, arcname=filename)

                        with status_lock:
                            status_db["completed_files"].append({
                                "url": f"{base_url}/download_single/{filename}",
                                "original": final_url,
                                "filename": filename,
                            })

                    except Exception as e:
                        set_status(error=f"Download error: {final_url} | {e}")

                    set_status(progress=i)

            set_status(zip_ready=True)

        finally:
            set_status(is_running=False, current_item="")

    background_tasks.add_task(worker, links)
    return {"status": "started"}


@app.get("/download")
def download():
    if os.path.exists(ZIP_FILENAME):
        return FileResponse(ZIP_FILENAME, filename="downloads.zip", media_type="application/zip")
    return {"error": "Datei noch nicht erstellt"}
