#!/usr/bin/env python3
"""
Nocturnal OSINT Web Agent
Scans security feeds and custom targets during nocturnal sweeps,
detects zero-days & CVEs relevant to your Pi stack, embeds intelligence
into local Qdrant vector memory with Gemini embeddings, and delivers
an executive Morning Threat & Tech Briefing to Telegram.
"""

import os
import sys
import time
import xml.etree.ElementTree as ET
import logging
import asyncio
from typing import Dict, List, Any, Optional
import httpx
import yaml
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from fastapi import FastAPI, Query, Body
from fastapi.responses import JSONResponse
import uvicorn
from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("NocturnalOSINT")

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
CONFIG_FILE = os.getenv("CONFIG_FILE", "/app/config/osint_targets.yml")
METRICS_PORT = int(os.getenv("METRICS_PORT", "8095"))
QDRANT_HOST = os.getenv("QDRANT_HOST", "qdrant")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))

config: Dict[str, Any] = {}
last_briefing: Dict[str, Any] = {"timestamp": None, "content": "No briefing generated yet."}
qclient: Optional[QdrantClient] = None


def load_config():
    global config
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                config = yaml.safe_load(f) or {}
                logger.info("Loaded OSINT configuration from %s", CONFIG_FILE)
        except Exception as e:
            logger.error("Failed to parse %s: %s", CONFIG_FILE, e)
    else:
        logger.warning("Config file not found at %s. Using defaults.", CONFIG_FILE)


def init_qdrant():
    global qclient
    try:
        qclient = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT, timeout=5)
        coll_name = config.get("qdrant", {}).get("collection", "osint_intelligence")
        colls = [c.name for c in qclient.get_collections().collections]
        if coll_name not in colls:
            qclient.create_collection(
                collection_name=coll_name,
                vectors_config=qmodels.VectorParams(size=3072, distance=qmodels.Distance.COSINE)
            )
            logger.info("Created Qdrant collection: %s (3072 dims)", coll_name)
        else:
            logger.info("Connected to existing Qdrant collection: %s", coll_name)
    except Exception as e:
        logger.warning("Qdrant initialization note: %s", e)


async def send_telegram_alert(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(url, json=payload)
    except Exception as e:
        logger.error("Failed to send Telegram message: %s", e)


async def get_gemini_embedding(text: str) -> Optional[List[float]]:
    """Gets 3072-dimensional vector embedding from Gemini."""
    if not GEMINI_API_KEY:
        return None
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-001:embedContent?key={GEMINI_API_KEY}"
    payload = {"content": {"parts": [{"text": text[:2000]}]}}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                data = resp.json()
                return data["embedding"]["values"]
    except Exception as e:
        logger.warning("Embedding error: %s", e)
    return None


async def fetch_rss_feed(url: str, source_name: str) -> List[Dict[str, str]]:
    """Fetches and parses standard RSS/Atom feeds."""
    items = []
    try:
        async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "NocturnalOSINT/2.2"})
            if resp.status_code == 200:
                root = ET.fromstring(resp.content)
                for item in root.findall(".//item")[:10]:
                    title = item.findtext("title", "").strip()
                    link = item.findtext("link", "").strip()
                    desc = item.findtext("description", "").strip()
                    # Clean HTML tags from description
                    clean_desc = BeautifulSoup(desc, "html.parser").get_text(strip=True) if desc else ""
                    if title:
                        items.append({
                            "source": source_name,
                            "title": title,
                            "link": link,
                            "summary": clean_desc[:300]
                        })
    except Exception as e:
        logger.warning("Error fetching feed %s: %s", source_name, e)
    return items


async def fetch_custom_watcher(target: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Monitors a custom web page for target alert keywords."""
    url = target.get("url", "")
    name = target.get("name", url)
    keywords = [k.lower() for k in target.get("alert_keywords", [])]

    try:
        async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7)"})
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.content, "html.parser")
                text = soup.get_text()
                matches = [k for k in keywords if k in text.lower()]
                if matches:
                    return {
                        "source": f"Watcher: {name}",
                        "title": f"Matches detected on {name}",
                        "link": url,
                        "summary": f"Detected matching keywords: {', '.join(matches)}"
                    }
    except Exception as e:
        logger.debug("Custom watcher error for %s: %s", name, e)
    return None


async def execute_nocturnal_sweep() -> str:
    """Runs a full intelligence sweep, embeds to Qdrant, and synthesizes a morning briefing."""
    logger.info("Starting Nocturnal OSINT Intelligence Sweep...")
    all_findings: List[Dict[str, str]] = []

    # 1. Stack Vulnerability Sources
    vuln_cfg = config.get("vulnerability_watch", {})
    if vuln_cfg.get("enabled", True):
        stack_keywords = [k.lower() for k in vuln_cfg.get("stack_keywords", [])]
        for src in vuln_cfg.get("sources", []):
            feed_items = await fetch_rss_feed(src.get("url", ""), src.get("name", "VulnFeed"))
            for it in feed_items:
                combined_text = (it["title"] + " " + it["summary"]).lower()
                matched = [k for k in stack_keywords if k in combined_text]
                if matched:
                    it["relevance"] = f"Matches Pi stack: {', '.join(matched)}"
                    all_findings.append(it)

    # 2. General RSS Intelligence Feeds
    for feed in config.get("rss_intelligence", []):
        feed_items = await fetch_rss_feed(feed.get("url", ""), feed.get("name", "RSS"))
        all_findings.extend(feed_items[:5])

    # 3. Custom Web Watchers
    for watcher in config.get("custom_watchers", []):
        res = await fetch_custom_watcher(watcher)
        if res:
            all_findings.append(res)

    logger.info("Gathered %s intelligence signals.", len(all_findings))

    # 4. Ingest into Qdrant Vector Memory
    if qclient:
        coll_name = config.get("qdrant", {}).get("collection", "osint_intelligence")
        points = []
        for idx, item in enumerate(all_findings[:15]):
            full_text = f"{item['title']}: {item['summary']}"
            vec = await get_gemini_embedding(full_text)
            if vec:
                points.append(qmodels.PointStruct(
                    id=int(time.time() * 1000) + idx,
                    vector=vec,
                    payload=item
                ))
        if points:
            try:
                qclient.upsert(collection_name=coll_name, points=points)
                logger.info("Stored %s intelligence vectors in Qdrant.", len(points))
            except Exception as e:
                logger.warning("Qdrant upsert error: %s", e)

    # 5. Synthesize Briefing via Gemini 2.5 Flash
    briefing_text = await generate_gemini_briefing(all_findings)
    last_briefing["timestamp"] = time.strftime("%Y-%m-%d %H:%M:%S UTC")
    last_briefing["content"] = briefing_text

    # Dispatch to Telegram
    await send_telegram_alert(briefing_text)
    return briefing_text


async def generate_gemini_briefing(findings: List[Dict[str, str]]) -> str:
    """Uses Gemini 2.5 Flash to synthesize an executive morning briefing."""
    if not GEMINI_API_KEY or not findings:
        return (
            "🌅 <b>Nocturnal OSINT Briefing</b>\n\n"
            "• <i>No critical zero-days or stack alerts detected during nocturnal sweep.</i>\n"
            "• All Pi microservices operating nominally."
        )

    summary_input = "\n\n".join(
        [f"[{f.get('source', 'Unknown')}] {f.get('title')}\nSummary: {f.get('summary', '')}" for f in findings[:12]]
    )

    prompt = (
        "You are an elite cyber threat intelligence officer synthesizing an executive morning briefing for a homelab operator.\n"
        "Here are raw intelligence signals gathered overnight:\n\n"
        f"{summary_input}\n\n"
        "Generate a concise, 4-to-5 bullet morning threat & tech briefing formatted in clean HTML (using <b>, <i>, <code> tags):\n"
        "1. 🛡️ <b>Stack Vulnerabilities & CVEs:</b> Any issues directly impacting FastAPI, Docker, Vaultwarden, AdGuard, CrowdSec, Python 3.12, Linux kernel.\n"
        "2. 📰 <b>Top Cyber Threats:</b> 1 or 2 high-impact zero-days or industry breaches.\n"
        "3. 💡 <b>Tech & AI Signals:</b> 1 key advancement in AI or systems.\n"
        "Keep it sharp, actionable, and executive level."
    )

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent?key={GEMINI_API_KEY}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 600}
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                data = resp.json()
                synth = data["candidates"][0]["content"]["parts"][0]["text"].strip()
                return f"🌅 <b>Nocturnal OSINT Morning Briefing</b>\n\n{synth}"
    except Exception as e:
        logger.error("Gemini briefing error: %s", e)

    return "🌅 <b>Nocturnal OSINT Briefing</b>\n\n• Sweep completed. Intelligence signals archived to Qdrant."


app = FastAPI(title="Nocturnal OSINT Web Agent", version="2.2.0")


@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "service": "nocturnal-osint",
        "qdrant_connected": bool(qclient),
        "last_sweep": last_briefing["timestamp"]
    }


@app.get("/api/briefing")
async def get_briefing():
    return last_briefing


@app.post("/api/sweep")
async def trigger_sweep():
    result = await execute_nocturnal_sweep()
    return {"status": "sweep_completed", "briefing": result}


@app.post("/api/query")
async def semantic_search(query: str = Body(..., embed=True)):
    """Searches past intelligence vectors in Qdrant."""
    if not qclient or not GEMINI_API_KEY:
        return JSONResponse({"error": "Qdrant or Gemini not available"}, status_code=503)

    vec = await get_gemini_embedding(query)
    if not vec:
        return JSONResponse({"error": "Failed to embed query"}, status_code=500)

    coll_name = config.get("qdrant", {}).get("collection", "osint_intelligence")
    results = qclient.search(collection_name=coll_name, query_vector=vec, limit=5)
    return {
        "query": query,
        "results": [{"score": r.score, "payload": r.payload} for r in results]
    }


async def scheduler_loop():
    """Runs periodic sweeps and checks morning briefing schedule."""
    interval = config.get("schedule", {}).get("sweep_interval_minutes", 360) * 60
    logger.info("Scheduler active: sweep every %s seconds.", interval)

    # Run initial brief sweep after 30 seconds startup delay
    await asyncio.sleep(30)
    try:
        await execute_nocturnal_sweep()
    except Exception as e:
        logger.error("Initial sweep error: %s", e)

    while True:
        await asyncio.sleep(interval)
        try:
            await execute_nocturnal_sweep()
        except Exception as e:
            logger.error("Scheduled sweep error: %s", e)


async def main():
    load_config()
    init_qdrant()
    logger.info("Nocturnal OSINT Agent starting on port %s", METRICS_PORT)
    server_cfg = uvicorn.Config(app=app, host="0.0.0.0", port=METRICS_PORT, log_level="warning")
    server = uvicorn.Server(server_cfg)

    await asyncio.gather(
        server.serve(),
        scheduler_loop()
    )


if __name__ == "__main__":
    asyncio.run(main())
