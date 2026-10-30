#!/usr/bin/env python3
"""
24/7 AI Sentinel & Ops Center for Raspberry Pi
- News & Stock Watcher (strict whole-word matching, publication freshness filter, batched digests)
- Interactive Telegram Command Center
- /pi: Real-time CPU temp, RAM, Disk, Docker container telemetry
- /scan <url>: Headless URL redirection & security inspector
- /cve <package>: Instant CVE & vulnerability lookup
- /trending: Trending AI/ML open-source GitHub repositories (+ 9:00 AM daily digest)
- /devices: Local Wi-Fi network scanner & proactive rogue device alerts
- /ping: Network latency diagnostics
- /age <hours>: Adjust maximum article age filter
"""

import os
import sys
import time
import re
import socket
import ssl
import json
import logging
import sqlite3
import hashlib
import threading
import shutil
import calendar
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
from html.parser import HTMLParser
from typing import Optional, List, Dict, Any, Tuple

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
logger = logging.getLogger("Sentinel")

# Load configuration
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

DEFAULT_KEYWORDS = [
    "NVDA", "AAPL", "TSLA", "MSFT", "GOOG", "AMZN", "META", "AMD",
    "OpenAI", "Anthropic", "zero-day", "ransomware", "earnings", "acquisition",
    "NSE", "NIFTY", "NIFTY50", "SENSEX", "BSE", "RELIANCE", "TCS", "INFY"
]
DEFAULT_INTERVAL = int(os.getenv("POLL_INTERVAL_MINUTES", "15"))
DEFAULT_MAX_AGE_HOURS = int(os.getenv("MAX_ARTICLE_AGE_HOURS", "24"))
MAX_ARTICLES_RETAINED = 2000
MAX_DB_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB hard cap

# Database path (persisted in ./data)
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "seen_articles.db")

# Threading lock for dynamic settings
state_lock = threading.Lock()
current_interval_minutes = DEFAULT_INTERVAL
current_max_age_hours = DEFAULT_MAX_AGE_HOURS
active_keywords: List[str] = list(DEFAULT_KEYWORDS)

# In-memory cache for MAC vendor lookups
mac_vendor_cache: Dict[str, str] = {
    "b8:27:eb": "Raspberry Pi", "dc:a6:32": "Raspberry Pi", "e4:5f:01": "Raspberry Pi",
    "28:cd:c1": "Raspberry Pi", "d8:3a:dd": "Raspberry Pi",
    "f0:18:98": "Apple", "a4:c3:f0": "Apple", "3c:22:fb": "Apple", "ac:bc:32": "Apple",
    "bc:d0:74": "Apple", "e0:acc:b": "Apple", "98:01:a7": "Apple", "14:7d:da": "Apple",
    "00:1a:11": "Google", "d8:6c:63": "Google", "54:60:09": "Google",
    "fc:65:de": "Amazon", "44:65:0d": "Amazon", "cc:9e:00": "Amazon",
    "24:0a:c4": "Espressif (IoT)", "30:ae:a4": "Espressif (IoT)", "84:0d:8e": "Espressif (IoT)"
}

# Default RSS Feeds (Global Tech & Indian / NSE Markets)
FEEDS = [
    {
        "name": "Yahoo Finance (US)",
        "url": "https://feeds.finance.yahoo.com/rss/2.0/headline?s=NVDA,AAPL,TSLA,MSFT,GOOG,AMZN,META,AMD&region=US&lang=en-US"
    },
    {
        "name": "Google News (Tech)",
        "url": "https://news.google.com/rss/search?q=Nvidia+OR+OpenAI+OR+Anthropic+OR+earnings&hl=en-US&gl=US&ceid=US:en"
    },
    {
        "name": "BleepingComputer",
        "url": "https://www.bleepingcomputer.com/feed/"
    },
    {
        "name": "Economic Times Markets (India)",
        "url": "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"
    },
    {
        "name": "Google News (NSE / Indian Markets)",
        "url": "https://news.google.com/rss/search?q=NSE+OR+BSE+OR+Nifty+OR+Sensex+OR+\"Indian+market\"&hl=en-IN&gl=IN&ceid=IN:en"
    },
    {
        "name": "Yahoo Finance (India / NSE)",
        "url": "https://feeds.finance.yahoo.com/rss/2.0/headline?s=^NSEI,^BSESN,RELIANCE.NS,TCS.NS,INFY.NS,HDFCBANK.NS,ICICIBANK.NS,SBIN.NS,BHARTIARTL.NS&region=IN&lang=en-IN"
    }
]


# ==========================================
# Database & Settings Management
# ==========================================

def init_db() -> bool:
    """Initializes SQLite tables for articles, settings, and known LAN devices with SD-card safeguards."""
    global current_interval_minutes, current_max_age_hours, active_keywords
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        # SD Card & Performance optimizations: Write-Ahead Logging + reduced fsync
        cursor.execute("PRAGMA journal_mode = WAL")
        cursor.execute("PRAGMA synchronous = NORMAL")

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

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS known_devices (
                mac TEXT PRIMARY KEY,
                ip TEXT,
                vendor TEXT,
                first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Clean up any Docker bridge entries from known_devices
        cursor.execute("DELETE FROM known_devices WHERE ip LIKE '172.%'")
        
        # Refresh vendor names for known devices
        cursor.execute("SELECT mac, ip FROM known_devices WHERE vendor = 'Unknown Vendor'")
        for row in cursor.fetchall():
            mac_val, ip_val = row[0], row[1]
            new_v = resolve_mac_vendor(mac_val, ip_val)
            if new_v != "Unknown Vendor":
                cursor.execute("UPDATE known_devices SET vendor = ? WHERE mac = ?", (new_v, mac_val))
        conn.commit()

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

        # Load saved max age hours
        cursor.execute("SELECT value FROM settings WHERE key = 'max_age_hours'")
        row = cursor.fetchone()
        if row:
            try:
                current_max_age_hours = int(row[0])
            except ValueError:
                pass
        else:
            cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('max_age_hours', ?)", (str(DEFAULT_MAX_AGE_HOURS),))

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
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
        conn.commit()


def maintain_database():
    """
    Enforces multiple safeguards to guarantee the database never exceeds a few megabytes:
    1. Removes records older than 14 days.
    2. Enforces a strict row-count ceiling of MAX_ARTICLES_RETAINED (2,000 entries).
    3. Runs VACUUM to reclaim freed disk pages so file shrinks.
    4. Emergency guard: If file size exceeds 10MB, trims aggressively and vacuums.
    """
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()

            # 1. Purge older than 14 days
            cursor.execute("DELETE FROM seen_articles WHERE seen_at < ?", (cutoff,))
            deleted_age = cursor.rowcount

            # 2. Hard row-count ceiling (keep newest 2,000)
            cursor.execute("""
                DELETE FROM seen_articles 
                WHERE id NOT IN (
                    SELECT id FROM seen_articles ORDER BY seen_at DESC LIMIT ?
                )
            """, (MAX_ARTICLES_RETAINED,))
            deleted_count = cursor.rowcount

            total_deleted = deleted_age + deleted_count
            conn.commit()

            # 3. Check physical file size & vacuum if needed
            db_size = os.path.getsize(DB_PATH) if os.path.exists(DB_PATH) else 0
            if total_deleted > 50 or db_size > MAX_DB_SIZE_BYTES:
                cursor.execute("VACUUM")
                conn.commit()
                new_size = os.path.getsize(DB_PATH) if os.path.exists(DB_PATH) else 0
                logger.info("Database maintained: pruned %d entries. Size: %.1f KB", total_deleted, new_size / 1024)
    except Exception as e:
        logger.warning("Database maintenance error: %s", e)


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


def get_article_datetime(entry) -> Optional[datetime]:
    """Extracts UTC publication timestamp from feed entry."""
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed:
        try:
            return datetime.fromtimestamp(calendar.timegm(parsed), timezone.utc)
        except Exception:
            pass
    return None


def format_relative_time(dt: Optional[datetime]) -> str:
    if not dt:
        return "Recent"
    now = datetime.now(timezone.utc)
    diff_secs = (now - dt).total_seconds()
    if diff_secs < 60:
        return "Just now"
    elif diff_secs < 3600:
        return f"{int(diff_secs / 60)}m ago"
    elif diff_secs < 86400:
        return f"{int(diff_secs / 3600)}h ago"
    else:
        return f"{int(diff_secs / 86400)}d ago"


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

    # Split long texts into paragraph chunks <= 3800 chars to prevent Telegram 400 Bad Request
    chunks = []
    if len(text) <= 3900:
        chunks = [text]
    else:
        paragraphs = text.split("\n\n")
        current_chunk = []
        current_len = 0
        for p in paragraphs:
            if current_len + len(p) + 2 > 3800 and current_chunk:
                chunks.append("\n\n".join(current_chunk))
                current_chunk = []
                current_len = 0
            current_chunk.append(p)
            current_len += len(p) + 2
        if current_chunk:
            chunks.append("\n\n".join(current_chunk))

    all_ok = True
    for chunk in chunks:
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": chunk,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }

        try:
            resp = requests.post(url, json=payload, timeout=10)
            if resp.status_code != 200:
                all_ok = False
                logger.error("Telegram error (%d): %s", resp.status_code, resp.text)
        except Exception as e:
            logger.error("Network error sending Telegram message: %s", e)
            all_ok = False
        time.sleep(0.3)

    return all_ok


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
# Core Feed Poller (With Publication Date Filter)
# ==========================================

def check_feeds(manual_trigger: bool = False):
    logger.info("Checking RSS feeds (manual=%s, max_age=%dh)...", manual_trigger, current_max_age_hours)
    matched_articles = []
    now_utc = datetime.now(timezone.utc)

    with state_lock:
        max_age = current_max_age_hours

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

            # Publication Date Freshness Filter
            article_dt = get_article_datetime(entry)
            if article_dt:
                age_hours = (now_utc - article_dt).total_seconds() / 3600.0
                if age_hours > max_age:
                    # Article is older than max freshness cutoff: mark seen and skip!
                    mark_seen(article_id, title, link, feed_name)
                    continue

            matched_kws = match_keywords(title, summary)
            if matched_kws:
                rel_time = format_relative_time(article_dt)
                matched_articles.append({
                    "title": title,
                    "link": link,
                    "source": feed_name,
                    "rel_time": rel_time,
                    "tags": [f"#{kw.upper()}" for kw in matched_kws[:3]]
                })

            mark_seen(article_id, title, link, feed_name)

    if not matched_articles:
        logger.info("Feed check completed. No new matches within %dh cutoff.", max_age)
        if manual_trigger:
            send_telegram_message(f"✅ <b>Manual Check Complete:</b> No new articles published within the last {max_age} hours.")
        return

    total_matched = len(matched_articles)
    # Never hide news articles: send all matched updates!
    lines = [
        f"📊 <b>Market & Tech Sentinel</b> (<code>{total_matched} new update{'s' if total_matched > 1 else ''}</code>)",
        ""
    ]

    for item in matched_articles:
        tags_str = " ".join(item["tags"])
        lines.append(f"• <b>{item['title']}</b>")
        lines.append(f"  <i>{item['source']}</i> • <code>{item['rel_time']}</code> | <code>{tags_str}</code>")
        lines.append(f"  🔗 <a href='{item['link']}'>Read Article</a>")
        lines.append("")

    digest_text = "\n".join(lines)
    send_telegram_message(digest_text)
    maintain_database()


def reschedule_job(minutes: int):
    global current_interval_minutes
    with state_lock:
        current_interval_minutes = minutes
        save_setting("interval_minutes", str(minutes))
        schedule.clear("feed_checker")
        schedule.every(minutes).minutes.do(check_feeds).tag("feed_checker")
    logger.info("Schedule updated: running every %d minutes", minutes)


# ==========================================
# Feature 1: /pi System & Hardware Telemetry
# ==========================================

def get_docker_containers() -> List[Dict[str, str]]:
    containers = []
    socket_path = "/var/run/docker.sock"
    if not os.path.exists(socket_path):
        return containers

    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(3)
        s.connect(socket_path)
        s.send(b"GET /containers/json HTTP/1.0\r\nHost: localhost\r\n\r\n")

        response = b""
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            response += chunk
        s.close()

        parts = response.split(b"\r\n\r\n", 1)
        if len(parts) == 2:
            data = json.loads(parts[1].decode("utf-8", errors="ignore"))
            for c in data:
                name = c.get("Names", [""])[0].lstrip("/")
                status = c.get("Status", "Unknown")
                containers.append({"name": name, "status": status})
    except Exception as e:
        logger.warning("Error reading docker socket: %s", e)

    return containers


def get_system_telemetry() -> str:
    cpu_temp = "N/A"
    temp_paths = [
        "/host/sys/class/thermal/thermal_zone0/temp",
        "/sys/class/thermal/thermal_zone0/temp"
    ]
    for p in temp_paths:
        if os.path.exists(p):
            try:
                with open(p) as f:
                    cpu_temp = f"{float(f.read().strip()) / 1000.0:.1f}°C"
                break
            except Exception:
                pass

    load_1, load_5, load_15 = "N/A", "N/A", "N/A"
    load_paths = ["/host/proc/loadavg", "/proc/loadavg"]
    for p in load_paths:
        if os.path.exists(p):
            try:
                with open(p) as f:
                    parts = f.read().split()
                    load_1, load_5, load_15 = parts[0], parts[1], parts[2]
                break
            except Exception:
                pass

    mem_used_mb, mem_total_mb, mem_pct = 0, 0, 0
    mem_paths = ["/host/proc/meminfo", "/proc/meminfo"]
    for p in mem_paths:
        if os.path.exists(p):
            try:
                with open(p) as f:
                    mem_data = {}
                    for line in f:
                        parts = line.split(":")
                        if len(parts) == 2:
                            mem_data[parts[0].strip()] = int(parts[1].strip().split()[0])
                    total = mem_data.get("MemTotal", 0)
                    avail = mem_data.get("MemAvailable", 0)
                    used = total - avail
                    mem_total_mb = int(total / 1024)
                    mem_used_mb = int(used / 1024)
                    mem_pct = int((used / total) * 100) if total else 0
                break
            except Exception:
                pass

    try:
        total, used, free = shutil.disk_usage("/")
        disk_total_gb = f"{total / (1024**3):.1f}GB"
        disk_used_gb = f"{used / (1024**3):.1f}GB"
        disk_pct = int((used / total) * 100)
    except Exception:
        disk_total_gb, disk_used_gb, disk_pct = "N/A", "N/A", 0

    uptime_str = "N/A"
    uptime_paths = ["/host/proc/uptime", "/proc/uptime"]
    for p in uptime_paths:
        if os.path.exists(p):
            try:
                with open(p) as f:
                    secs = float(f.read().split()[0])
                    uptime_str = str(timedelta(seconds=int(secs)))
                break
            except Exception:
                pass

    tailscale_ip = "Offline"
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("100.100.100.100", 80))
        tailscale_ip = s.getsockname()[0]
        s.close()
    except Exception:
        pass

    containers = get_docker_containers()
    container_lines = []
    for c in containers:
        container_lines.append(f"  • <code>{c['name']}</code>: <i>{c['status'][:20]}</i>")

    c_summary = "\n".join(container_lines) if container_lines else "  • <i>No containers detected</i>"

    msg = (
        f"🖥️ <b>Raspberry Pi Telemetry</b>\n"
        f"• <b>Host:</b> <code>{os.uname().nodename}</code>\n"
        f"• <b>Uptime:</b> <code>{uptime_str}</code>\n"
        f"• <b>CPU Temp:</b> <b>{cpu_temp}</b>\n"
        f"• <b>Load:</b> <code>{load_1}, {load_5}, {load_15}</code>\n"
        f"• <b>RAM:</b> <code>{mem_used_mb}MB / {mem_total_mb}MB ({mem_pct}%)</code>\n"
        f"• <b>Storage:</b> <code>{disk_used_gb} / {disk_total_gb} ({disk_pct}%)</code>\n"
        f"• <b>Tailscale IP:</b> <code>{tailscale_ip}</code>\n\n"
        f"🐳 <b>Docker Containers ({len(containers)}):</b>\n{c_summary}"
    )
    return msg


# ==========================================
# Feature 2: /scan <url> Headless Security Inspector
# ==========================================

def inspect_url_security(target_url: str) -> str:
    if not target_url.startswith(("http://", "https://")):
        target_url = "https://" + target_url

    parsed = urlparse(target_url)
    domain = parsed.hostname
    if not domain:
        return "⚠️ Invalid URL provided. Example: <code>/scan https://bit.ly/xyz</code>"

    try:
        ip = socket.gethostbyname(domain)
    except Exception as e:
        return f"❌ <b>DNS Resolution Failed:</b> <code>{e}</code>"

    try:
        resp = requests.get(
            target_url,
            allow_redirects=True,
            timeout=8,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) SentinelSecurity/1.0"}
        )

        chain = []
        for r in resp.history:
            chain.append(f"  ↳ [{r.status_code}] <code>{r.url[:50]}</code>")
        chain.append(f"  🏁 [{resp.status_code}] <b>{resp.url}</b>")

        content_type = resp.headers.get("Content-Type", "Unknown").split(";")[0]
        content_length = resp.headers.get("Content-Length")
        size_str = f"{int(content_length) / 1024:.1f} KB" if content_length else "Stream / Chunked"

        threat_flags = []
        dangerous_exts = (".exe", ".sh", ".apk", ".dmg", ".bat", ".vbs", ".zip", ".iso")
        if any(resp.url.lower().endswith(ext) for ext in dangerous_exts):
            threat_flags.append("⚠️ <b>Direct Executable/Archive Download Detected</b>")

        if not resp.url.startswith("https://"):
            threat_flags.append("⚠️ <b>Unencrypted HTTP connection</b>")

        hsts = "Yes" if "Strict-Transport-Security" in resp.headers else "No"
        xframe = resp.headers.get("X-Frame-Options", "None")
        threat_summary = "\n".join(threat_flags) if threat_flags else "✅ No obvious payload red-flags detected."

        msg = (
            f"🔍 <b>URL Security Inspection</b>\n\n"
            f"🌐 <b>Target:</b> <code>{domain}</code> (IP: <code>{ip}</code>)\n"
            f"📡 <b>Redirect Path ({len(resp.history)} jumps):</b>\n" + "\n".join(chain) + "\n\n"
            f"📋 <b>Payload Metadata:</b>\n"
            f"• <b>MIME Type:</b> <code>{content_type}</code>\n"
            f"• <b>Size:</b> <code>{size_str}</code>\n"
            f"• <b>HSTS Strict:</b> <code>{hsts}</code>\n"
            f"• <b>X-Frame:</b> <code>{xframe}</code>\n\n"
            f"🛡️ <b>Heuristics:</b>\n{threat_summary}"
        )
        return msg
    except Exception as e:
        return f"❌ <b>Inspection Error:</b> <code>{str(e)[:150]}</code>"


# ==========================================
# Feature 3: /cve <package> Vulnerability Lookup
# ==========================================

def lookup_cve(package_name: str) -> str:
    pkg = package_name.strip()
    found_vulns = []

    for eco in ("PyPI", "npm", "Go", "crates.io"):
        try:
            url = "https://api.osv.dev/v1/query"
            payload = {"package": {"name": pkg, "ecosystem": eco}}
            resp = requests.post(url, json=payload, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                vulns = data.get("vulns", [])
                for v in vulns:
                    vid = v.get("id", "N/A")
                    summary = v.get("summary") or (v.get("details", "")[:80] + "...")
                    aliases = ", ".join(v.get("aliases", [])[:2])
                    found_vulns.append({
                        "id": vid,
                        "ecosystem": eco,
                        "aliases": aliases,
                        "summary": summary
                    })
                if len(found_vulns) >= 5:
                    break
        except Exception:
            pass

    if not found_vulns:
        return f"✅ <b>Vulnerability Check:</b> No public CVE advisories found for <code>{pkg}</code> in major ecosystems."

    lines = [f"🛡️ <b>Vulnerabilities Found for <code>{pkg}</code>:</b>\n"]
    for v in found_vulns[:4]:
        alias_str = f" ({v['aliases']})" if v['aliases'] else ""
        lines.append(f"• <b>{v['id']}{alias_str}</b> [<code>{v['ecosystem']}</code>]")
        lines.append(f"  <i>{v['summary']}</i>")
        lines.append("")

    if len(found_vulns) > 4:
        lines.append(f"<i>(+{len(found_vulns) - 4} older advisories recorded)</i>")

    return "\n".join(lines)


# ==========================================
# Feature 4: /trending & 9 AM AI Digest
# ==========================================

def get_trending_ai_repos() -> str:
    try:
        url = "https://api.github.com/search/repositories?q=topic:llm+stars:>300&sort=updated&order=desc&per_page=4"
        resp = requests.get(url, timeout=6, headers={"User-Agent": "SentinelBot/1.0"})
        if resp.status_code == 200:
            items = resp.json().get("items", [])
            if not items:
                return "ℹ️ No trending repos retrieved."

            lines = ["🔥 <b>Trending AI & LLM GitHub Repositories</b>\n"]
            for r in items[:4]:
                name = r.get("full_name")
                stars = r.get("stargazers_count", 0)
                desc = r.get("description", "No description") or ""
                link = r.get("html_url")
                lines.append(f"• <b>{name}</b> (⭐ <code>{stars:,}</code>)")
                lines.append(f"  <i>{desc[:90]}...</i>")
                lines.append(f"  🔗 <a href='{link}'>GitHub Repo</a>")
                lines.append("")

            return "\n".join(lines)
    except Exception as e:
        return f"❌ Error querying GitHub: {e}"

    return "⚠️ Could not retrieve trending repositories."


def send_daily_ai_briefing():
    logger.info("Sending scheduled 9:00 AM AI briefing & Market Snapshot...")
    market = get_indian_market_summary()
    report = get_trending_ai_repos()
    send_telegram_message(f"🌅 <b>Daily Morning Intelligence Briefing</b>\n\n{market}\n\n<b>Trending AI & ML Repositories:</b>\n{report}")


# ==========================================
# Feature 4b: Stock & Market Intelligence (NSE & Global)
# ==========================================

def get_quote(symbol: str) -> Optional[Dict[str, Any]]:
    """Fetches real-time stock/index quote from Yahoo Finance API (NSE, BSE, US)."""
    sym = symbol.upper().strip()
    aliases = {
        'NIFTY': '^NSEI',
        'NIFTY50': '^NSEI',
        'SENSEX': '^BSESN',
        'BANKNIFTY': '^NSEBANK',
        'NIFTYBANK': '^NSEBANK',
        'NIFTYIT': '^CNXIT',
    }
    target_sym = aliases.get(sym, sym)

    candidates = [target_sym]
    if '.' not in target_sym and not target_sym.startswith('^'):
        # For plain tickers, prioritize Indian NSE (.NS), then BSE (.BO), then US / global
        candidates = [f"{target_sym}.NS", target_sym, f"{target_sym}.BO"]

    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
    for s in candidates:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{s}?interval=1d"
        try:
            resp = requests.get(url, headers=headers, timeout=6)
            if resp.status_code != 200:
                continue
            data = resp.json()
            res = data.get("chart", {}).get("result")
            if not res:
                continue
            meta = res[0]["meta"]
            price = meta.get("regularMarketPrice")
            if price is None:
                continue
            prev = meta.get("chartPreviousClose", meta.get("previousClose", price))
            chg = price - prev if prev else 0.0
            pct = (chg / prev) * 100 if prev else 0.0
            currency = meta.get("currency", "INR")
            high = meta.get("regularMarketDayHigh", price)
            low = meta.get("regularMarketDayLow", price)
            name = meta.get("shortName") or meta.get("longName") or s

            if s.endswith(".NS"):
                exchange = "NSE (National Stock Exchange)"
            elif s.endswith(".BO"):
                exchange = "BSE (Bombay Stock Exchange)"
            elif s.startswith("^"):
                exchange = "Benchmark Index"
            else:
                exchange = meta.get("exchangeName", "Global Market")

            return {
                "symbol": s,
                "name": name,
                "price": price,
                "change": chg,
                "pct": pct,
                "currency": currency,
                "high": high,
                "low": low,
                "prev": prev,
                "exchange": exchange
            }
        except Exception:
            continue
    return None


def format_quote_message(q: Dict[str, Any]) -> str:
    sym = q["symbol"]
    curr = "₹" if q["currency"] == "INR" else ("$" if q["currency"] == "USD" else f"{q['currency']} ")
    chg = q["change"]
    pct = q["pct"]
    icon = "🟢" if chg >= 0 else "🔴"
    sign = "+" if chg >= 0 else ""

    lines = [
        f"📈 <b>{q['name']}</b> (<code>{sym}</code>)",
        f"• <b>Price:</b> <code>{curr}{q['price']:,.2f}</code>",
        f"• <b>Change:</b> {icon} <code>{sign}{chg:,.2f} ({sign}{pct:.2f}%)</code>",
        f"• <b>Day Range:</b> <code>{curr}{q['low']:,.2f}</code> - <code>{curr}{q['high']:,.2f}</code>",
        f"• <b>Prev Close:</b> <code>{curr}{q['prev']:,.2f}</code>",
        f"• <b>Exchange:</b> <code>{q['exchange']}</code>"
    ]
    return "\n".join(lines)


def get_indian_market_summary() -> str:
    """Provides a live snapshot of major Indian benchmark indices & trading session status."""
    indices = [
        ("NIFTY 50", "^NSEI"),
        ("S&P BSE SENSEX", "^BSESN"),
        ("BANK NIFTY", "^NSEBANK"),
        ("NIFTY IT", "^CNXIT")
    ]
    lines = ["🇮🇳 <b>Indian Stock Market Snapshot (NSE / BSE)</b>", ""]
    for label, sym in indices:
        q = get_quote(sym)
        if q:
            icon = "🟢" if q["change"] >= 0 else "🔴"
            sign = "+" if q["change"] >= 0 else ""
            lines.append(f"• <b>{label}:</b> <code>{q['price']:,.2f}</code> {icon} (<code>{sign}{q['pct']:.2f}%</code>)")
        else:
            lines.append(f"• <b>{label}:</b> <i>Data temporarily unavailable</i>")

    lines.append("")
    # Check if equity market is currently open in IST (UTC+5:30)
    # Standard equity hours: 09:15 AM to 03:30 PM IST, Monday to Friday
    now_ist = datetime.now(timezone(timedelta(hours=5, minutes=30)))
    is_weekday = now_ist.weekday() < 5
    market_open = now_ist.replace(hour=9, minute=15, second=0, microsecond=0)
    market_close = now_ist.replace(hour=15, minute=30, second=0, microsecond=0)

    if is_weekday and market_open <= now_ist <= market_close:
        status_text = "🟢 <b>Market Status: OPEN</b> (Trading session active)"
    else:
        status_text = "⚪ <b>Market Status: CLOSED</b> (Next session: 9:15 AM IST)"

    lines.append(f"🕒 <i>IST: {now_ist.strftime('%d %b %Y, %I:%M %p')}</i>")
    lines.append(status_text)
    lines.append("\n<i>Tip: Check any stock with <code>/stock &lt;ticker&gt;</code> (e.g. <code>/stock RELIANCE</code> or <code>/stock TCS</code>).</i>")
    return "\n".join(lines)


# ==========================================
# Feature 5: /devices & Wi-Fi Watchdog
# ==========================================

def get_tz_name() -> str:
    """Returns local timezone abbreviation (e.g. IST)."""
    return time.tzname[0] if time.tzname else "IST"


def format_device_time(ts_str: Optional[str]) -> str:
    """Converts a stored timestamp into a human-readable local time string (IST)."""
    if not ts_str:
        return "Unknown"
    try:
        if "T" in ts_str:
            dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        else:
            dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        local_dt = dt.astimezone()
        tz_name = get_tz_name()
        now = datetime.now().astimezone()
        time_fmt = local_dt.strftime("%I:%M %p")
        if local_dt.date() == now.date():
            return f"Today at {time_fmt} {tz_name}"
        elif (now.date() - local_dt.date()).days == 1:
            return f"Yesterday at {time_fmt} {tz_name}"
        else:
            date_fmt = local_dt.strftime("%b %d, %I:%M %p")
            return f"{date_fmt} {tz_name}"
    except Exception:
        return ts_str


def is_randomized_mac(mac: str) -> bool:
    """Checks IEEE 802 Locally Administered / Private MAC address bit (used by iOS, macOS, Android)."""
    try:
        first_byte = int(mac.split(":")[0], 16)
        return (first_byte & 0x02) != 0
    except Exception:
        return False


def resolve_mac_vendor(mac: str, ip: str = "") -> str:
    mac_clean = mac.lower().strip()
    prefix = mac_clean[:8]

    if ip == "192.168.1.1":
        return "Gateway / Router (Nokia)"

    if prefix in mac_vendor_cache:
        return mac_vendor_cache[prefix]

    # Check for Apple / Android Private Randomized MAC
    if is_randomized_mac(mac_clean):
        label = "Private MAC (Apple/Mobile)"
        mac_vendor_cache[prefix] = label
        return label

    # Query public MAC OUI database
    try:
        clean_hex = mac_clean.replace(":", "")[:6]
        resp = requests.get(f"https://api.maclookup.app/v2/macs/{clean_hex}", timeout=3)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("found") and data.get("company"):
                comp = data["company"].split(",")[0].replace("Co.", "").replace("Ltd.", "").strip()[:25]
                mac_vendor_cache[prefix] = comp
                return comp
    except Exception:
        pass

    return "Unknown Vendor"


def is_lan_ip(ip: str) -> bool:
    """Filters out Docker virtual bridges (172.16.x-172.31.x) and loopback."""
    if ip.startswith(("127.", "169.254.")):
        return False
    # Docker default bridges
    if ip.startswith(("172.17.", "172.18.", "172.19.", "172.20.", "172.21.", "172.22.", "172.23.", "172.24.", "172.25.")):
        return False
    return True


def scan_wifi_devices() -> List[Dict[str, str]]:
    devices = []
    arp_paths = ["/host/proc/net/arp", "/proc/net/arp"]

    for p in arp_paths:
        if os.path.exists(p):
            try:
                with open(p) as f:
                    lines = f.readlines()[1:]
                    for line in lines:
                        parts = line.split()
                        if len(parts) >= 6:
                            ip = parts[0]
                            mac = parts[3].lower()
                            flags = parts[2]
                            if mac != "00:00:00:00:00:00" and flags != "0x0" and is_lan_ip(ip):
                                vendor = resolve_mac_vendor(mac, ip)
                                devices.append({"ip": ip, "mac": mac, "vendor": vendor})
                break
            except Exception as e:
                logger.warning("Error reading ARP table: %s", e)

    return devices


def check_for_new_devices():
    active_devices = scan_wifi_devices()
    if not active_devices:
        return

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        for dev in active_devices:
            mac = dev["mac"]
            ip = dev["ip"]
            vendor = dev["vendor"]

            cursor.execute("SELECT first_seen FROM known_devices WHERE mac = ?", (mac,))
            row = cursor.fetchone()

            if not row:
                cursor.execute(
                    "INSERT INTO known_devices (mac, ip, vendor, first_seen, last_seen) VALUES (?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
                    (mac, ip, vendor)
                )
                conn.commit()

                now_local_str = datetime.now().strftime("%I:%M:%S %p") + f" {get_tz_name()}"
                alert_msg = (
                    f"🚨 <b>New Device Connected to Wi-Fi!</b>\n\n"
                    f"• <b>IP:</b> <code>{ip}</code>\n"
                    f"• <b>MAC:</b> <code>{mac}</code>\n"
                    f"• <b>Vendor:</b> <i>{vendor}</i>\n"
                    f"• <b>Time:</b> <code>{now_local_str}</code>"
                )
                send_telegram_message(alert_msg)
            else:
                cursor.execute("UPDATE known_devices SET last_seen = CURRENT_TIMESTAMP, ip = ? WHERE mac = ?", (ip, mac))
                conn.commit()


# ==========================================
# Feature 6: /ping Network Diagnostics
# ==========================================

def ping_network_latency() -> str:
    targets = [("Cloudflare DNS", "1.1.1.1", 53), ("Google DNS", "8.8.8.8", 53)]
    results = []

    for name, host, port in targets:
        latencies = []
        for _ in range(3):
            t0 = time.time()
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(2.0)
                s.connect((host, port))
                s.close()
                latencies.append((time.time() - t0) * 1000.0)
            except Exception:
                pass
            time.sleep(0.1)

        if latencies:
            avg_ms = sum(latencies) / len(latencies)
            results.append(f"• <b>{name}</b> (<code>{host}</code>): <code>{avg_ms:.1f} ms</code>")
        else:
            results.append(f"• <b>{name}</b> (<code>{host}</code>): ❌ <i>Timeout</i>")

    return "📶 <b>Home Network Latency Test</b>\n\n" + "\n".join(results)


# ==========================================
# Feature 7: /web <url> & /ask <prompt> (Cost-Free)
# ==========================================

class ArticleExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text = []
        self.skip = False
        self.title = ""
        self.in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "nav", "header", "footer", "aside"):
            self.skip = True
        elif tag == "title":
            self.in_title = True

    def handle_endtag(self, tag):
        if tag in ("script", "style", "nav", "header", "footer", "aside"):
            self.skip = False
        elif tag == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.title += data.strip()
        elif not self.skip:
            d = data.strip()
            if len(d) > 25:
                self.text.append(d)


def extract_web_content(target_url: str) -> str:
    if not target_url.startswith(("http://", "https://")):
        target_url = "https://" + target_url

    parsed = urlparse(target_url)
    if not parsed.hostname:
        return "⚠️ Invalid URL provided. Example: <code>/web https://techcrunch.com/...</code>"

    try:
        resp = requests.get(target_url, timeout=10, headers={"User-Agent": "Mozilla/5.0 SentinelReader/1.0"})
        if resp.status_code != 200:
            return f"❌ Failed to fetch page (HTTP {resp.status_code})"

        parser = ArticleExtractor()
        parser.feed(resp.text)
        title = parser.title or parsed.hostname
        full_text = " ".join(parser.text)
        words = full_text.split()
        word_count = len(words)
        read_time = max(1, int(word_count / 200))

        # Optional Gemini AI Summary
        if GEMINI_API_KEY and full_text:
            prompt = (
                "You are an executive research assistant.\n"
                f"Webpage Title: {title}\n"
                f"Content: {full_text[:3500]}\n\n"
                "Provide an executive 3-bullet summary focusing on key takeaways, technical/financial facts, and relevance:"
            )
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.2, "maxOutputTokens": 250}
            }
            try:
                ai_resp = requests.post(url, json=payload, timeout=12)
                if ai_resp.status_code == 200:
                    ai_text = ai_resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
                    return (
                        f"📰 <b>Executive Web Summary</b>\n\n"
                        f"<b>{title}</b>\n"
                        f"<i>Source: {parsed.hostname} • ~{read_time} min read ({word_count} words)</i>\n\n"
                        f"🤖 <b>Key Takeaways:</b>\n{ai_text}\n\n"
                        f"🔗 <a href='{target_url}'>Original Article</a>"
                    )
            except Exception:
                pass

        # Local excerpt fallback (100% free with zero keys needed)
        excerpt = " ".join(parser.text[:3])[:350] + "..." if parser.text else "No main paragraph text extracted."
        return (
            f"📰 <b>Web Page Summary</b>\n\n"
            f"<b>{title}</b>\n"
            f"<i>Source: {parsed.hostname} • ~{read_time} min read ({word_count} words)</i>\n\n"
            f"<b>Excerpt:</b>\n<i>{excerpt}</i>\n\n"
            f"🔗 <a href='{target_url}'>Visit Page</a>"
        )
    except Exception as e:
        return f"❌ Error extracting web content: {str(e)[:150]}"


# ==========================================
# Interactive Telegram Command Listener
# ==========================================

def handle_telegram_command(command_text: str):
    global current_interval_minutes, current_max_age_hours, active_keywords
    parts = command_text.strip().split()
    if not parts:
        return

    cmd = parts[0].lower()
    args = parts[1:]

    # 1. /pi Telemetry
    if cmd in ("/pi", "/sys", "/telemetry"):
        send_telegram_message(get_system_telemetry())

    # 2. /scan <url>
    elif cmd == "/scan":
        if not args:
            send_telegram_message("⚠️ Usage: <code>/scan &lt;url&gt;</code> (e.g. <code>/scan https://bit.ly/xyz</code>)")
            return
        send_telegram_message("🔎 <i>Inspecting URL headers and redirects...</i>")
        send_telegram_message(inspect_url_security(args[0]))

    # 3. /cve <package>
    elif cmd == "/cve":
        if not args:
            send_telegram_message("⚠️ Usage: <code>/cve &lt;package_name&gt;</code> (e.g. <code>/cve fastapi</code>)")
            return
        send_telegram_message(f"🛡️ <i>Searching vulnerability databases for {args[0]}...</i>")
        send_telegram_message(lookup_cve(args[0]))

    # 4. /trending
    elif cmd in ("/trending", "/repos"):
        send_telegram_message("🔥 <i>Pulling trending open-source AI repos...</i>")
        send_telegram_message(get_trending_ai_repos())

    # 5. /devices
    elif cmd in ("/devices", "/wifi", "/lan"):
        devs = scan_wifi_devices()
        active_macs = {d["mac"]: d for d in devs}

        # Query all known devices with their first_seen timestamps
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT mac, ip, vendor, first_seen, last_seen FROM known_devices ORDER BY last_seen DESC")
            all_known = cursor.fetchall()

        if not devs and not all_known:
            send_telegram_message("ℹ️ No active LAN devices currently detected.")
            return

        lines = [f"📶 <b>Home Network Devices ({len(devs)} active):</b>\n"]

        # Track which MACs we display
        displayed_macs = set()

        # Display active devices first
        for d in devs:
            mac = d["mac"]
            ip = d["ip"]
            vendor = d["vendor"]
            displayed_macs.add(mac)

            # Get first_seen from database
            first_seen_str = "Recently"
            with sqlite3.connect(DB_PATH) as conn:
                cur = conn.cursor()
                cur.execute("SELECT first_seen FROM known_devices WHERE mac = ?", (mac,))
                row = cur.fetchone()
                if row and row[0]:
                    first_seen_str = format_device_time(row[0])

            lines.append(f"• <b>{ip}</b> | <i>{vendor}</i> (🟢 Active)")
            lines.append(f"  MAC: <code>{mac}</code>")
            lines.append(f"  🕒 <b>First Connected:</b> <code>{first_seen_str}</code>")
            lines.append("")

        # Display any previously known devices that are currently offline
        offline_count = 0
        for row in all_known:
            mac, ip, vendor, first_seen, last_seen = row
            if mac not in displayed_macs and is_lan_ip(ip):
                if offline_count == 0:
                    lines.append("<i>Previously Seen Devices:</i>")
                offline_count += 1
                if offline_count <= 5:
                    first_seen_str = format_device_time(first_seen)
                    lines.append(f"• <b>{ip}</b> | <i>{vendor}</i> (⚪ Offline)")
                    lines.append(f"  🕒 <b>First Connected:</b> <code>{first_seen_str}</code>")
                    lines.append("")

        send_telegram_message("\n".join(lines))

    # 6. /ping
    elif cmd in ("/ping", "/speed"):
        send_telegram_message(ping_network_latency())

    # 7. /web <url>
    elif cmd == "/web":
        if not args:
            send_telegram_message("⚠️ Usage: <code>/web &lt;url&gt;</code> (e.g. <code>/web https://techcrunch.com/...</code>)")
            return
        send_telegram_message("📖 <i>Extracting article & generating executive summary...</i>")
        send_telegram_message(extract_web_content(args[0]))

    # 8. /interval <mins>
    elif cmd == "/interval":
        if not args:
            send_telegram_message(
                f"⏱️ <b>Current Polling Interval:</b> <code>{current_interval_minutes} minutes</code>\n\n"
                f"To change it, send:\n<code>/interval &lt;minutes&gt;</code> (e.g. <code>/interval 30</code>)"
            )
            return

        try:
            mins = int(args[0])
            if mins < 1 or mins > 1440:
                send_telegram_message("⚠️ Please specify an interval between <b>1</b> and <b>1440</b> minutes.")
                return

            reschedule_job(mins)
            send_telegram_message(f"⏱️ <b>Interval Updated!</b>\nSentinel checks feeds every <b>{mins} minutes</b>.")
        except ValueError:
            send_telegram_message("⚠️ Invalid format. Example: <code>/interval 30</code>")

    # 8. /age <hours> or /freshness <hours>
    elif cmd in ("/age", "/freshness"):
        if not args:
            send_telegram_message(
                f"🕒 <b>Article Freshness Cutoff:</b> <code>{current_max_age_hours} hours</code>\n\n"
                f"Articles older than {current_max_age_hours}h are automatically ignored.\n"
                f"To change cutoff: <code>/age &lt;hours&gt;</code> (e.g. <code>/age 12</code> or <code>/age 24</code>)"
            )
            return

        try:
            hours = int(args[0])
            if hours < 1 or hours > 168:
                send_telegram_message("⚠️ Please specify an age cutoff between <b>1</b> and <b>168</b> hours (7 days).")
                return

            with state_lock:
                current_max_age_hours = hours
                save_setting("max_age_hours", str(hours))

            send_telegram_message(
                f"🕒 <b>Freshness Cutoff Updated!</b>\n"
                f"Sentinel will strictly reject any articles published older than <b>{hours} hours</b>."
            )
        except ValueError:
            send_telegram_message("⚠️ Invalid format. Example: <code>/age 24</code>")

    # 9. /check
    elif cmd in ("/check", "/poll"):
        send_telegram_message("🔄 <i>Running immediate RSS check...</i>")
        check_feeds(manual_trigger=True)

    # 10. /status
    elif cmd in ("/status", "/info"):
        with state_lock:
            kws_preview = ", ".join(active_keywords[:8])
            if len(active_keywords) > 8:
                kws_preview += f" (+{len(active_keywords) - 8} more)"
            curr_int = current_interval_minutes
            curr_age = current_max_age_hours

        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM seen_articles")
            total_arts = c.fetchone()[0]
            c.execute("SELECT COUNT(*) FROM known_devices")
            total_devs = c.fetchone()[0]

        db_size_kb = int(os.path.getsize(DB_PATH) / 1024) if os.path.exists(DB_PATH) else 0

        msg = (
            f"📊 <b>Sentinel Master Status</b>\n"
            f"• <b>Host:</b> <code>{os.uname().nodename}</code>\n"
            f"• <b>Cadence:</b> Every <code>{curr_int} mins</code>\n"
            f"• <b>Max Article Age:</b> <code>{curr_age} hours</code>\n"
            f"• <b>Active Watchlist:</b> {kws_preview}\n"
            f"• <b>Articles Tracked:</b> <code>{total_arts}</code> (<code>{db_size_kb} KB</code> / 2k cap)\n"
            f"• <b>Known Wi-Fi Devices:</b> <code>{total_devs}</code>\n"
            f"• <b>Uptime Kuma:</b> <code>http://192.168.1.10:3001</code>"
        )
        send_telegram_message(msg)

    # 11. /list
    elif cmd in ("/list", "/keywords"):
        with state_lock:
            kws = list(active_keywords)
        send_telegram_message(
            f"📋 <b>Watchlist Keywords ({len(kws)}):</b>\n"
            f"<code>{', '.join(kws)}</code>\n\n"
            f"Add keyword/ticker: <code>/add RELIANCE</code>\n"
            f"Remove keyword/ticker: <code>/remove TSLA</code>\n"
            f"Check live quote: <code>/stock RELIANCE</code>"
        )

    # 12. /stock <ticker> or /quote <ticker>
    elif cmd in ("/stock", "/quote", "/price"):
        if not args:
            send_telegram_message("⚠️ Usage: <code>/stock &lt;ticker&gt;</code>\n\nExamples:\n• <code>/stock RELIANCE</code>\n• <code>/stock TCS</code>\n• <code>/stock INFY</code>\n• <code>/stock NVDA</code>")
            return
        sym = args[0].strip()
        send_telegram_message(f"🔍 <i>Fetching live quote for {sym.upper()}...</i>")
        q = get_quote(sym)
        if q:
            send_telegram_message(format_quote_message(q))
        else:
            send_telegram_message(f"❌ Could not find quote for <b>{sym.upper()}</b>. Check ticker spelling (e.g. <code>/stock INFY</code> or <code>/stock AAPL</code>).")

    # 13. /nse or /market or /nifty
    elif cmd in ("/nse", "/market", "/nifty"):
        send_telegram_message("📊 <i>Fetching Indian market benchmark snapshot...</i>")
        send_telegram_message(get_indian_market_summary())

    # 14. /add <word>
    elif cmd == "/add":
        if not args:
            send_telegram_message("⚠️ Usage: <code>/add &lt;word&gt;</code>")
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

    # 15. /remove <word>
    elif cmd in ("/remove", "/del"):
        if not args:
            send_telegram_message("⚠️ Usage: <code>/remove &lt;word&gt;</code>")
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

    # 16. /help
    elif cmd in ("/help", "/start"):
        help_text = (
            f"🛡️ <b>Sentinel Ops Command Menu</b>\n\n"
            f"<b>Stocks & Market Intelligence:</b>\n"
            f"• <code>/stock &lt;ticker&gt;</code> - Real-time stock quote (NSE, BSE, US)\n"
            f"• <code>/nse</code> or <code>/market</code> - Live NIFTY 50, SENSEX, BANK NIFTY & session status\n"
            f"• <code>/list</code> - Show active news watchlist keywords\n"
            f"• <code>/add &lt;word&gt;</code> - Add ticker or topic to news alerts (e.g. /add RELIANCE)\n"
            f"• <code>/remove &lt;word&gt;</code> - Remove keyword from watchlist\n\n"
            f"<b>Hardware & Network:</b>\n"
            f"• <code>/pi</code> - Real-time CPU temp, RAM, Disk, & Docker\n"
            f"• <code>/devices</code> - List active devices on home Wi-Fi\n"
            f"• <code>/ping</code> - Test internet latency to 1.1.1.1\n\n"
            f"<b>Security & Developer Tools:</b>\n"
            f"• <code>/scan &lt;url&gt;</code> - Headless redirect & safety inspector\n"
            f"• <code>/web &lt;url&gt;</code> - Extract & summarize any article\n"
            f"• <code>/cve &lt;pkg&gt;</code> - Instant vulnerability lookup\n"
            f"• <code>/trending</code> - Top trending open-source AI repos\n\n"
            f"<b>Feed & Intelligence Control:</b>\n"
            f"• <code>/interval &lt;mins&gt;</code> - Change check frequency\n"
            f"• <code>/age &lt;hours&gt;</code> - Set max article age cutoff (e.g. /age 24)\n"
            f"• <code>/check</code> - Run an instant feed check now\n"
            f"• <code>/status</code> - Complete bot status overview"
        )
        send_telegram_message(help_text)


def telegram_listener():
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

                    if sender_chat_id == str(TELEGRAM_CHAT_ID) and text:
                        logger.info("Received Telegram command: %s", text)
                        handle_telegram_command(text)
                    else:
                        logger.warning("Ignored message from unauthorized chat: %s", sender_chat_id)
            elif resp.status_code == 409:
                time.sleep(5)
            else:
                time.sleep(2)

        except requests.exceptions.Timeout:
            continue
        except Exception as e:
            logger.warning("Telegram listener exception: %s", e)
            time.sleep(3)


def send_startup_ping():
    msg = (
        f"⚡ <b>Sentinel Command Center Online</b>\n"
        f"• Host: <code>{os.uname().nodename}</code>\n"
        f"• Cadence: <code>Every {current_interval_minutes}m</code>\n"
        f"• Freshness Cutoff: <code>Last {current_max_age_hours}h only</code>\n"
        f"• Tailscale Exit Node: <code>Active</code>\n"
        f"• Uptime Kuma: <code>http://192.168.1.10:3001</code>"
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

    # Schedule recurring feed checker
    reschedule_job(current_interval_minutes)

    # Schedule daily 9:00 AM AI repo briefing
    schedule.every().day.at("09:00").do(send_daily_ai_briefing)

    # Schedule daily 3:00 AM database vacuum & deep maintenance
    schedule.every().day.at("03:00").do(maintain_database)

    # Schedule Wi-Fi rogue device sweep every 5 minutes
    schedule.every(5).minutes.do(check_for_new_devices)

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
