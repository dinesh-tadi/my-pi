#!/usr/bin/env python3
"""
24/7 AI Stock & Tech News Watcher for Raspberry Pi (Interactive Edition)
Polls RSS feeds for tickers & tech news, delivers clean digests,
and accepts interactive management commands directly from Telegram (/interval, /status, /check, etc.).
"""

import os
import sys
import time
import re
import logging
import sqlite3
import hashlib
import threading
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

DEFAULT_KEYWORDS = [
    "NVDA", "AAPL", "TSLA", "MSFT", "GOOG", "AMZN", "META", "AMD",
    "OpenAI", "Anthropic", "zero-day", "ransomware", "earnings", "acquisition"
]
DEFAULT_INTERVAL = int(os.getenv("POLL_INTERVAL_MINUTES", "15"))

# Database path (persisted in ./data)
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "seen_articles.db")

# Threading lock for dynamic settings
state_lock = threading.Lock()
current_interval_minutes = DEFAULT_INTERVAL
active_keywords: List[str] = list(DEFAULT_KEYWORDS)

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


# ==========================================
# Database & Settings Management
# ==========================================

def init_db() -> bool:
    """Initializes SQLite tables for articles and persistent settings."""
    global current_interval_minutes, active_keywords
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        # Articles table
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

        # Settings table for interactive Telegram commands
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)

        # Load saved interval
        cursor.execute("SELECT value FROM settings WHERE key = 'interval_minutes'")
        row = cursor.fetchone()
        if row:
            try:
                current_interval_minutes = int(row[0])
            except ValueError:
                pass
        else:
            cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('interval_minutes', ?)", (str(DEFAULT_INTERVAL),))

        # Load saved keywords
        cursor.execute("SELECT value FROM settings WHERE key = 'keywords'")
        row = cursor.fetchone()
        if row and row[0].strip():
            active_keywords = [k.strip() for k in row[0].split(",") if k.strip()]
        else:
            cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('keywords', ?)", (",".join(DEFAULT_KEYWORDS),))

        cursor.execute("SELECT COUNT(*) FROM seen_articles")
        article_count = cursor.fetchone()[0]
        conn.commit()
        return article_count == 0


def save_setting(key: str, value: str):
    """Persists a configuration setting into SQLite."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
        conn.commit()


def get_db_stats() -> Dict[str, Any]:
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM seen_articles")
        total_articles = cursor.fetchone()[0]
        return {"total_articles": total_articles}


def clean_old_records(days: int = 30):
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM seen_articles WHERE seen_at < ?", (cutoff,))
            conn.commit()
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
    seed = (link or title).strip()
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def match_keywords(title: str, summary: str) -> List[str]:
    target_text = f"{title} {summary[:250]}"
    matches = []
    with state_lock:
        kws = list(active_keywords)

    for kw in kws:
        pattern = r"\b" + re.escape(kw) + r"\b"
        if re.search(pattern, target_text, re.IGNORECASE):
            matches.append(kw)

    return matches


# ==========================================
# Telegram Communication
# ==========================================

def send_telegram_message(text: str) -> bool:
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
    logger.info("First run detected: Seeding existing articles quietly...")
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
    logger.info("Seeded %d existing articles.", seeded)


# ==========================================
# Core Feed Poller
# ==========================================

def check_feeds(manual_trigger: bool = False):
    logger.info("Checking RSS feeds (manual=%s)...", manual_trigger)
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

            matched_kws = match_keywords(title, summary)
            if matched_kws:
                matched_articles.append({
                    "title": title,
                    "link": link,
                    "source": feed_name,
                    "tags": [f"#{kw.upper()}" for kw in matched_kws[:3]]
                })

            mark_seen(article_id, title, link, feed_name)

    if not matched_articles:
        logger.info("Feed check completed. No new matches.")
        if manual_trigger:
            send_telegram_message("✅ <b>Manual Check Complete:</b> No new articles matching your watchlist.")
        return

    total_matched = len(matched_articles)
    articles_to_send = matched_articles[:3]

    logger.info("Found %d matched new articles. Delivering digest...", total_matched)

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


# ==========================================
# Dynamic Scheduler
# ==========================================

def reschedule_job(minutes: int):
    """Dynamically updates the recurring schedule."""
    global current_interval_minutes
    with state_lock:
        current_interval_minutes = minutes
        save_setting("interval_minutes", str(minutes))
        schedule.clear("feed_checker")
        schedule.every(minutes).minutes.do(check_feeds).tag("feed_checker")
    logger.info("Schedule updated: running every %d minutes", minutes)


# ==========================================
# Interactive Telegram Command Listener
# ==========================================

def handle_telegram_command(command_text: str):
    """Parses and executes commands sent from the authorized Telegram chat."""
    global current_interval_minutes, active_keywords
    parts = command_text.strip().split()
    if not parts:
        return

    cmd = parts[0].lower()
    args = parts[1:]

    # Command: /interval <mins>
    if cmd == "/interval":
        if not args:
            send_telegram_message(
                f"⏱️ <b>Current Polling Interval:</b> <code>{current_interval_minutes} minutes</code>\n\n"
                f"To change it, send:\n<code>/interval &lt;minutes&gt;</code> (e.g. <code>/interval 30</code>)"
            )
            return

        try:
            mins = int(args[0])
            if mins < 1 or mins > 1440:
                send_telegram_message("⚠️ Please specify an interval between <b>1</b> and <b>1440</b> minutes (24h).")
                return

            reschedule_job(mins)
            send_telegram_message(
                f"⏱️ <b>Interval Updated!</b>\n"
                f"Sentinel will now check RSS feeds every <b>{mins} minutes</b>."
            )
        except ValueError:
            send_telegram_message("⚠️ Invalid format. Example usage: <code>/interval 30</code>")

    # Command: /check
    elif cmd in ("/check", "/poll"):
        send_telegram_message("🔄 <i>Running immediate RSS check...</i>")
        check_feeds(manual_trigger=True)

    # Command: /status
    elif cmd in ("/status", "/info"):
        stats = get_db_stats()
        with state_lock:
            kws_preview = ", ".join(active_keywords[:8])
            if len(active_keywords) > 8:
                kws_preview += f" (+{len(active_keywords) - 8} more)"
            curr_int = current_interval_minutes

        msg = (
            f"📊 <b>Sentinel Bot Status</b>\n"
            f"• <b>Host:</b> <code>{os.uname().nodename}</code>\n"
            f"• <b>Cadence:</b> Every <code>{curr_int} mins</code>\n"
            f"• <b>Active Watchlist:</b> {kws_preview}\n"
            f"• <b>Articles Indexed:</b> <code>{stats['total_articles']}</code>\n"
            f"• <b>Feeds Active:</b> <code>{len(FEEDS)} feeds</code>"
        )
        send_telegram_message(msg)

    # Command: /list
    elif cmd in ("/list", "/keywords"):
        with state_lock:
            kws = list(active_keywords)
        send_telegram_message(
            f"📋 <b>Watchlist Keywords ({len(kws)}):</b>\n"
            f"<code>{', '.join(kws)}</code>\n\n"
            f"Add ticker: <code>/add INTEL</code>\n"
            f"Remove ticker: <code>/remove TSLA</code>"
        )

    # Command: /add <word>
    elif cmd == "/add":
        if not args:
            send_telegram_message("⚠️ Usage: <code>/add &lt;ticker_or_word&gt;</code> (e.g. <code>/add INTEL</code>)")
            return
        new_word = args[0].strip()
        with state_lock:
            if new_word.lower() not in [k.lower() for k in active_keywords]:
                active_keywords.append(new_word)
                save_setting("keywords", ",".join(active_keywords))
                msg = f"✅ Added <b>{new_word}</b> to your watchlist."
            else:
                msg = f"ℹ️ <b>{new_word}</b> is already in your watchlist."
        send_telegram_message(msg)

    # Command: /remove <word>
    elif cmd in ("/remove", "/del"):
        if not args:
            send_telegram_message("⚠️ Usage: <code>/remove &lt;ticker_or_word&gt;</code>")
            return
        del_word = args[0].strip().lower()
        with state_lock:
            new_list = [k for k in active_keywords if k.lower() != del_word]
            if len(new_list) < len(active_keywords):
                active_keywords = new_list
                save_setting("keywords", ",".join(active_keywords))
                msg = f"🗑️ Removed <b>{del_word.upper()}</b> from your watchlist."
            else:
                msg = f"ℹ️ <b>{del_word}</b> was not found in your watchlist."
        send_telegram_message(msg)

    # Command: /help or /start
    elif cmd in ("/help", "/start"):
        help_text = (
            f"🤖 <b>Sentinel Command Center</b>\n\n"
            f"• <code>/interval &lt;mins&gt;</code> - Change check frequency (e.g. <code>/interval 30</code>)\n"
            f"• <code>/check</code> - Run an instant feed check right now\n"
            f"• <code>/status</code> - View uptime, interval, and DB stats\n"
            f"• <code>/list</code> - Show all active watchlist tickers\n"
            f"• <code>/add &lt;word&gt;</code> - Add keyword (e.g. <code>/add CRWD</code>)\n"
            f"• <code>/remove &lt;word&gt;</code> - Remove keyword (e.g. <code>/remove TSLA</code>)\n"
            f"• <code>/help</code> - Show this command list"
        )
        send_telegram_message(help_text)


def telegram_listener():
    """Background long-polling loop to receive incoming Telegram commands."""
    logger.info("Telegram command listener thread started.")
    offset = 0

    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
            params = {"offset": offset, "timeout": 20}
            resp = requests.get(url, params=params, timeout=25)

            if resp.status_code == 200:
                data = resp.json()
                for update in data.get("result", []):
                    offset = update["update_id"] + 1
                    msg = update.get("message") or update.get("edited_message")
                    if not msg:
                        continue

                    sender_chat_id = str(msg.get("chat", {}).get("id"))
                    text = msg.get("text", "").strip()

                    # Security check: only authorized chat ID can issue commands
                    if sender_chat_id == str(TELEGRAM_CHAT_ID) and text:
                        logger.info("Received Telegram command: %s", text)
                        handle_telegram_command(text)
                    else:
                        logger.warning("Ignored message from unauthorized chat: %s", sender_chat_id)
            elif resp.status_code == 409:
                # Another instance is polling; back off briefly
                time.sleep(5)
            else:
                time.sleep(2)

        except requests.exceptions.Timeout:
            # Long-poll timed out normally, loop again
            continue
        except Exception as e:
            logger.warning("Telegram listener exception: %s", e)
            time.sleep(3)


def send_startup_ping():
    msg = (
        f"🟢 <b>Sentinel Interactive Center Online</b>\n"
        f"• Host: <code>{os.uname().nodename}</code>\n"
        f"• Interval: <code>Every {current_interval_minutes}m</code>\n\n"
        f"💡 <i>You can now control this bot directly from Telegram!</i>\n"
        f"Send <code>/help</code> or <code>/interval 30</code> to try it out."
    )
    send_telegram_message(msg)


# ==========================================
# Main Entry Point
# ==========================================

def main():
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.error("Please configure TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env!")
        sys.exit(1)

    is_fresh = init_db()

    if is_fresh:
        seed_database_quietly()

    # Start recurring schedule
    reschedule_job(current_interval_minutes)

    # Launch Telegram command listener in a background daemon thread
    listener_thread = threading.Thread(target=telegram_listener, daemon=True)
    listener_thread.start()

    send_startup_ping()

    logger.info("Main loop running. Waiting for scheduled tasks...")
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
