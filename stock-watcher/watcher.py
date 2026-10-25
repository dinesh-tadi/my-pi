#!/usr/bin/env python3
"""
24/7 AI Stock & Tech News Watcher for Raspberry Pi
Polls RSS feeds for tickers & tech news, runs optional Gemini AI analysis,
and delivers clean, batched alerts to your Telegram bot.
"""

import os
import sys
import time
import re
import logging
import sqlite3
import hashlib
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

import requests
import feedparser
import schedule
from dotenv import load_dotenv

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("StockWatcher")

# Load configuration
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

raw_keywords = os.getenv(
    "WATCHLIST_KEYWORDS",
    "NVDA,AAPL,TSLA,MSFT,GOOG,AMZN,META,AMD,OpenAI,Anthropic,zero-day,ransomware,earnings,acquisition"
)
# Clean and filter keywords
KEYWORDS = [k.strip() for k in raw_keywords.split(",") if k.strip()]

POLL_INTERVAL_MINUTES = int(os.getenv("POLL_INTERVAL_MINUTES", "15"))

# Database path (persisted in ./data)
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "seen_articles.db")

# Default RSS Feeds
FEEDS = [
    {
        "name": "Yahoo Finance",
        "url": "https://feeds.finance.yahoo.com/rss/2.0/headline?s=NVDA,AAPL,TSLA,MSFT,GOOG,AMZN,META,AMD&region=US&lang=en-US"
    },
    {
        "name": "Google News",
        "url": "https://news.google.com/rss/search?q=Nvidia+OR+OpenAI+OR+Anthropic+OR+earnings&hl=en-US&gl=US&ceid=US:en"
    },
    {
        "name": "BleepingComputer",
        "url": "https://www.bleepingcomputer.com/feed/"
    }
]


def init_db() -> bool:
    """Initializes the SQLite deduplication database. Returns True if fresh/empty."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS seen_articles (
                id TEXT PRIMARY KEY,
                title TEXT,
                link TEXT,
                source TEXT,
                seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_seen_at ON seen_articles (seen_at)")
        cursor.execute("SELECT COUNT(*) FROM seen_articles")
        count = cursor.fetchone()[0]
        conn.commit()
        return count == 0


def clean_old_records(days: int = 30):
    """Purges records older than 30 days to keep the SD card database tiny."""
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM seen_articles WHERE seen_at < ?", (cutoff,))
            deleted = cursor.rowcount
            conn.commit()
            if deleted > 0:
                logger.info("Cleaned up %d old records from database", deleted)
    except Exception as e:
        logger.warning("Error pruning database: %s", e)


def is_seen(article_id: str) -> bool:
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM seen_articles WHERE id = ?", (article_id,))
        return cursor.fetchone() is not None


def mark_seen(article_id: str, title: str, link: str, source: str):
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR IGNORE INTO seen_articles (id, title, link, source) VALUES (?, ?, ?, ?)",
            (article_id, title, link, source)
        )
        conn.commit()


def generate_article_id(title: str, link: str) -> str:
    """Creates a deterministic hash ID from link or title."""
    seed = (link or title).strip()
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def match_keywords(title: str, summary: str) -> List[str]:
    """
    Strict whole-word regex matching against title (primary) and summary.
    Prevents false positives like 'ai' inside 'email' or 'remain'.
    """
    # Prioritize title, add snippet of summary
    target_text = f"{title} {summary[:250]}"
    matches = []

    for kw in KEYWORDS:
        # Match whole word with word boundaries
        pattern = r"\b" + re.escape(kw) + r"\b"
        if re.search(pattern, target_text, re.IGNORECASE):
            matches.append(kw)

    return matches


def send_telegram_message(text: str) -> bool:
    """Sends an HTML formatted message to your Telegram bot."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.error("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing!")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }

    try:
        resp = requests.post(url, json=payload, timeout=10)
        return resp.status_code == 200
    except Exception as e:
        logger.error("Network error sending Telegram message: %s", e)
        return False


def seed_database_quietly():
    """On first run, mark all current feed entries as seen so we don't spam historical backlog."""
    logger.info("First run detected: Seeding existing articles without sending alerts...")
    seeded = 0
    for feed_info in FEEDS:
        try:
            feed = feedparser.parse(feed_info["url"])
            for entry in feed.entries:
                title = entry.get("title", "").strip()
                link = entry.get("link", "").strip()
                if title and link:
                    article_id = generate_article_id(title, link)
                    mark_seen(article_id, title, link, feed_info["name"])
                    seeded += 1
        except Exception as e:
            logger.warning("Error seeding feed %s: %s", feed_info["name"], e)
    logger.info("Seeded %d existing articles. Future checks will only alert on brand new articles.", seeded)


def check_feeds():
    """Polls RSS feeds, filters strictly, and sends a concise batched digest."""
    logger.info("Checking RSS feeds...")
    matched_articles = []

    for feed_info in FEEDS:
        feed_name = feed_info["name"]
        feed_url = feed_info["url"]

        try:
            feed = feedparser.parse(feed_url)
        except Exception as e:
            logger.warning("Error fetching feed '%s': %s", feed_name, e)
            continue

        for entry in feed.entries:
            title = entry.get("title", "").strip()
            link = entry.get("link", "").strip()
            summary = entry.get("summary", "").strip()

            if not title or not link:
                continue

            article_id = generate_article_id(title, link)
            if is_seen(article_id):
                continue

            # Strict word boundary match
            matched_kws = match_keywords(title, summary)
            if matched_kws:
                matched_articles.append({
                    "title": title,
                    "link": link,
                    "source": feed_name,
                    "tags": [f"#{kw.upper()}" for kw in matched_kws[:3]]
                })

            # Mark seen regardless so we don't evaluate again
            mark_seen(article_id, title, link, feed_name)

    if not matched_articles:
        logger.info("Feed check completed. No new matches.")
        return

    # Cap to max 3 articles per cycle to avoid notification overload
    total_matched = len(matched_articles)
    articles_to_send = matched_articles[:3]

    logger.info("Found %d matched new articles. Delivering digest...", total_matched)

    # Format into a single clean digest message
    lines = [
        f"📊 <b>Market & Tech Sentinel</b> (<code>{len(articles_to_send)} update{'s' if len(articles_to_send) > 1 else ''}</code>)",
        ""
    ]

    for item in articles_to_send:
        tags_str = " ".join(item["tags"])
        lines.append(f"• <b>{item['title']}</b>")
        lines.append(f"  <i>{item['source']}</i> | <code>{tags_str}</code>")
        lines.append(f"  🔗 <a href='{item['link']}'>Read Article</a>")
        lines.append("")

    if total_matched > 3:
        lines.append(f"<i>(+{total_matched - 3} other matches filtered out)</i>")

    digest_text = "\n".join(lines)
    send_telegram_message(digest_text)
    clean_old_records()


def send_startup_ping():
    """Pings Telegram to confirm the bot is active."""
    node_name = os.uname().nodename
    msg = (
        f"🟢 <b>Sentinel Filter Active</b>\n"
        f"Host: <code>{node_name}</code>\n"
        f"Cadence: <code>Every {POLL_INTERVAL_MINUTES}m (Batched Digest)</code>\n"
        f"Watchlist: <code>{len(KEYWORDS)} keywords (Strict Whole-Word)</code>"
    )
    send_telegram_message(msg)


def main():
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.error("Please configure TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env!")
        sys.exit(1)

    is_fresh = init_db()

    if is_fresh:
        # Seed existing backlog so we don't spam 50 historical notifications on boot
        seed_database_quietly()

    send_startup_ping()

    schedule.every(POLL_INTERVAL_MINUTES).minutes.do(check_feeds)
    logger.info("Scheduler started: running every %d minutes", POLL_INTERVAL_MINUTES)

    while True:
        try:
            schedule.run_pending()
            time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Shutting down watcher...")
            break
        except Exception as e:
            logger.error("Unexpected error in main loop: %s", e)
            time.sleep(10)


if __name__ == "__main__":
    main()
