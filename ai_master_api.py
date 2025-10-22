"""
serper_ai_api.py
Tek dosyalı Serper.dev tabanlı AI API
------------------------------------
pip install fastapi uvicorn requests cachetools python-dotenv

Render / local çalıştırma:
SERPER_API_KEY=your_key uvicorn serper_ai_api:app --host 0.0.0.0 --port $PORT
"""

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from cachetools import TTLCache
import requests, os, time, threading
from datetime import datetime
from typing import Dict, Any, Optional, List

# -------- CONFIG --------
app = FastAPI(title="Serper AI API", version="2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)

# Direct environment variable (no .env required)
SERPER_API_KEY = os.getenv("SERPER_API_KEY")
if not SERPER_API_KEY:
    raise RuntimeError("SERPER_API_KEY is required in environment variables")

# Cache ve Rate-limit
CACHE = TTLCache(maxsize=1024, ttl=600)
RATE_LOCK = threading.Lock()
RATE_STORE: Dict[str, List[float]] = {}
RATE_LIMIT = 30
RATE_WINDOW = 60  # saniye

# -------- MODELS --------
class AskRequest(BaseModel):
    query: str
    mode: Optional[str] = "auto"  # auto, search, news, images, scholar, shopping, places
    num: Optional[int] = 5

# -------- RATE LIMIT --------
def check_rate(ip: str):
    now = time.time()
    with RATE_LOCK:
        timestamps = RATE_STORE.get(ip, [])
        timestamps = [t for t in timestamps if now - t < RATE_WINDOW]
        if len(timestamps) >= RATE_LIMIT:
            raise HTTPException(status_code=429, detail="Rate limit exceeded")
        timestamps.append(now)
        RATE_STORE[ip] = timestamps

# -------- CACHING --------
def get_cache(k: str):
    return CACHE.get(k)

def set_cache(k: str, v):
    CACHE[k] = v

# -------- DETECT MODE AUTO --------
def detect_mode(q: str) -> str:
    ql = q.lower()
    if any(w in ql for w in ["haber", "news", "bugün", "güncel", "son dakika"]):
        return "news"
    if any(w in ql for w in ["foto", "image", "resim", "görsel", "goster"]):
        return "images"
    if any(w in ql for w in ["makale", "scholar", "akademik", "bilimsel"]):
        return "scholar"
    if any(w in ql for w in ["ürün", "fiyat", "al", "satın", "shopping", "alışveriş"]):
        return "shopping"
    if any(w in ql for w in ["nerede", "yakın", "place", "restoran", "otel", "konum"]):
        return "places"
    return "search"

# -------- LOCAL SUMMARIZER --------
def summarize_results(items: List[Dict[str, Any]], mode: str) -> str:
    if not items:
        return "Sonuç bulunamadı."
    texts = []
    for i, item in enumerate(items[:5]):
        if "title" in item:
            line = f"{i+1}. {item['title']}"
            if "snippet" in item:
                line += f" — {item['snippet'][:180]}..."
            texts.append(line)
        elif "name" in item:
            texts.append(f"{i+1}. {item['name']} ({item.get('address', '')})")
    joined = "\n".join(texts)
    if mode == "news":
        return f"📰 En son haberler:\n{joined}"
    if mode == "images":
        return f"🖼️ Görsel sonuçlar ({len(items)} adet bulundu)"
    if mode == "scholar":
        return f"📚 Akademik sonuç özeti:\n{joined}"
    if mode == "shopping":
        return f"🛍️ Ürün önerileri:\n{joined}"
    if mode == "places":
        return f"📍 Yakın yerler:\n{joined}"
    return f"🔍 Web arama özeti:\n{joined}"

# -------- CALL SERPER API --------
def serper_call(mode: str, query: str, num: int = 5):
    endpoint = f"https://google.serper.dev/{mode}"
    headers = {"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"}
    payload = {"q": query, "num": num}
    try:
        r = requests.post(endpoint, headers=headers, json=payload, timeout=15)
        r.raise_for_status()
        data = r.json()
        key_map = {
            "search": "organic",
            "news": "news",
            "images": "images",
            "scholar": "organic",
            "shopping": "shopping",
            "places": "places"
        }
        items = data.get(key_map.get(mode, "organic"), [])
        return {"items": items, "raw": data}
    except requests.RequestException as e:
        return {"error": str(e), "items": []}

# -------- MAIN ENDPOINT --------
@app.post("/ask")
async def ask(req: AskRequest, request: Request):
    ip = request.client.host if request.client else "unknown"
    check_rate(ip)

    q = req.query.strip()
    if not q:
        raise HTTPException(status_code=400, detail="Query boş olamaz")

    mode = req.mode.lower() if req.mode else "auto"
    if mode == "auto":
        mode = detect_mode(q)

    cache_key = f"{mode}::{q}"
    cached = get_cache(cache_key)
    if cached:
        return {"cached": True, "result": cached}

    res = serper_call(mode, q, req.num)
    if "error" in res:
        raise HTTPException(status_code=502, detail=res["error"])

    summary = summarize_results(res["items"], mode)
    result = {
        "mode": mode,
        "query": q,
        "summary": summary,
        "count": len(res["items"]),
        "items": res["items"],
        "timestamp": datetime.utcnow().isoformat()
    }

    set_cache(cache_key, result)
    return {"cached": False, "result": result}

# -------- HEALTH & ROOT --------
@app.get("/")
def root():
    return {
        "status": "running",
        "time": datetime.utcnow().isoformat(),
        "endpoints": ["/ask"],
        "supported_modes": ["search", "news", "images", "scholar", "shopping", "places"]
    }

@app.get("/health")
def health():
    return {"ok": True, "serper": bool(SERPER_API_KEY)}
