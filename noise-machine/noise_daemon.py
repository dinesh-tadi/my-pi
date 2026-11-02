#!/usr/bin/env python3
"""
Noise Machine - ISP & Surveillance Profile Poisoning Daemon
Periodically issues benign, diverse HTTP/HTTPS requests with authentic
User-Agent rotation, human jitter delays, and wide domain diversity to destroy
ISP/commercial tracking profiles.
"""

import os
import sys
import time
import random
import asyncio
import logging
from typing import List, Dict, Any
import httpx
from fastapi import FastAPI
import uvicorn
from dotenv import load_dotenv

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("NoiseMachine")

load_dotenv()

METRICS_PORT = int(os.getenv("METRICS_PORT", "8092"))
MIN_DELAY_SECS = int(os.getenv("MIN_DELAY_SECS", "45"))
MAX_DELAY_SECS = int(os.getenv("MAX_DELAY_SECS", "150"))

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_6_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Safari/605.1.15",
]

TARGET_ENDPOINTS = [
    {"name": "Wikipedia Random", "url": "https://en.wikipedia.org/api/rest_v1/page/random/summary"},
    {"name": "arXiv Query", "url": "https://export.arxiv.org/api/query?search_query=all:quantum&max_results=1"},
    {"name": "OpenAlex Works", "url": "https://api.openalex.org/works/random"},
    {"name": "NASA Astronomy", "url": "https://api.nasa.gov/planetary/apod?api_key=DEMO_KEY"},
    {"name": "Project Gutenberg", "url": "https://gutendex.com/books/?search=history"},
    {"name": "GitHub Trending API", "url": "https://api.github.com/repositories?since=364"},
    {"name": "OpenLibrary Literature", "url": "https://openlibrary.org/subjects/history.json?limit=5"},
    {"name": "US National Weather", "url": "https://api.weather.gov/points/38.8894,-77.0352"},
]

stats = {
    "total_queries": 0,
    "successful_queries": 0,
    "failed_queries": 0,
    "last_endpoint": "None",
    "last_query_time": 0,
    "start_time": time.time(),
}

app = FastAPI(title="Noise Machine Telemetry Poisoner", version="2.2.0")


@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "service": "noise-machine",
        "uptime_seconds": int(time.time() - stats["start_time"]),
        "total_queries": stats["total_queries"]
    }


@app.get("/metrics")
async def metrics():
    return {
        "stats": stats,
        "active_user_agents": len(USER_AGENTS),
        "target_endpoints": [e["name"] for e in TARGET_ENDPOINTS]
    }


async def run_noise_loop():
    logger.info("Starting background noise generation loop...")
    while True:
        target = random.choice(TARGET_ENDPOINTS)
        ua = random.choice(USER_AGENTS)
        headers = {
            "User-Agent": ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1"
        }

        stats["total_queries"] += 1
        stats["last_endpoint"] = target["name"]
        stats["last_query_time"] = time.time()

        try:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                resp = await client.get(target["url"], headers=headers)
                if resp.status_code in (200, 204, 301, 302):
                    stats["successful_queries"] += 1
                    logger.info("Decoy noise query: %s -> HTTP %s", target["name"], resp.status_code)
                else:
                    stats["failed_queries"] += 1
                    logger.warning("Decoy query status %s: %s", resp.status_code, target["name"])
        except Exception as e:
            stats["failed_queries"] += 1
            logger.debug("Decoy request suppressed exception: %s", e)

        sleep_secs = random.randint(MIN_DELAY_SECS, MAX_DELAY_SECS)
        await asyncio.sleep(sleep_secs)


async def main():
    logger.info("Initializing Noise Machine Daemon on port %s", METRICS_PORT)
    config = uvicorn.Config(app=app, host="0.0.0.0", port=METRICS_PORT, log_level="warning")
    server = uvicorn.Server(config)

    await asyncio.gather(
        server.serve(),
        run_noise_loop()
    )


if __name__ == "__main__":
    asyncio.run(main())
