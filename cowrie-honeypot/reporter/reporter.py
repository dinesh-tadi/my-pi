#!/usr/bin/env python3
"""
Cowrie Honeypot Threat Reporter
Tails Cowrie JSON logs, performs GeoIP enrichment, runs optional Gemini AI analysis,
and dispatches real-time intrusion reports to your Telegram bot.
"""

import os
import sys
import time
import json
import logging
from typing import Dict, Any, Optional

import requests
from dotenv import load_dotenv

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("CowrieReporter")

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
LOG_FILE_PATH = os.getenv("LOG_FILE_PATH", "/logs/cowrie.json")

# In-memory session tracking: session_id -> dict
sessions: Dict[str, Dict[str, Any]] = {}


def send_telegram_message(text: str) -> bool:
    """Sends an HTML formatted message to Telegram."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram credentials not configured!")
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
        logger.error("Failed sending Telegram message: %s", e)
        return False


def get_geoip_info(ip: str) -> str:
    """Fetches country, city, and ISP for a given IP."""
    if ip in ("127.0.0.1", "localhost") or ip.startswith("192.168.") or ip.startswith("10."):
        return "Internal LAN / Localhost"
    try:
        resp = requests.get(f"http://ip-api.com/json/{ip}?fields=country,city,isp,org", timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            parts = [data.get("city"), data.get("country"), data.get("isp")]
            return ", ".join([p for p in parts if p])
    except Exception:
        pass
    return "Unknown Location"


def analyze_attack_with_gemini(session_data: Dict[str, Any]) -> Optional[str]:
    """Uses Gemini to evaluate threat sophistication, payload intent, and CVE targets."""
    if not GEMINI_API_KEY:
        return None

    commands = session_data.get("commands", [])
    credentials = session_data.get("credentials", [])
    downloads = session_data.get("downloads", [])

    if not commands and not credentials and not downloads:
        return None

    prompt = (
        "You are an expert security incident analyst evaluating an SSH honeypot capture.\n"
        f"Attacker IP: {session_data.get('src_ip')} ({session_data.get('geo')})\n"
        f"Credentials Attempted: {credentials[:10]}\n"
        f"Commands Executed: {commands[:15]}\n"
        f"File Downloads: {downloads}\n\n"
        "Provide a concise, 2-to-3 sentence executive assessment:\n"
        "- Attacker Type: (Automated Bot / Worm / Manual Operator)\n"
        "- Probable Tooling & Intent: (e.g. Mirai variant, SSH brute-force, reverse shell dropper)\n"
        "- Threat Level: [LOW / MEDIUM / HIGH / CRITICAL]"
    )

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 200}
    }

    try:
        resp = requests.post(url, json=payload, timeout=12)
        if resp.status_code == 200:
            data = resp.json()
            return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as e:
        logger.warning("Gemini threat analysis error: %s", e)

    return None


def dispatch_session_report(session_id: str):
    """Compiles and sends the intrusion report when a session closes."""
    session = sessions.pop(session_id, None)
    if not session:
        return

    # Skip sessions with zero activity (e.g., instant TCP port probe with no login attempt)
    if not session["credentials"] and not session["commands"] and not session["downloads"]:
        return

    src_ip = session.get("src_ip", "Unknown")
    geo = session.get("geo", "Unknown")
    duration = int(time.time() - session.get("start_time", time.time()))

    lines = [
        "🪤 <b>Honeypot Intrusion Report</b>",
        f"🌐 <b>Attacker IP:</b> <code>{src_ip}</code>",
        f"📍 <b>Location:</b> <i>{geo}</i>",
        f"⏱️ <b>Duration:</b> <code>{duration}s</code>",
        ""
    ]

    if session["credentials"]:
        creds_sample = ", ".join(
            [f"<code>{u}:{p}</code>" for u, p in session["credentials"][:5]]
        )
        lines.append(f"🔑 <b>Credentials Tried:</b> {creds_sample}")

    if session["commands"]:
        cmds_formatted = "\n".join([f"  $ <code>{cmd}</code>" for cmd in session["commands"][:8]])
        lines.extend(["💻 <b>Commands Captured:</b>", cmds_formatted])

    if session["downloads"]:
        lines.append(f"📦 <b>Downloads:</b> {len(session['downloads'])} payload(s)")

    # AI Analysis
    ai_analysis = analyze_attack_with_gemini(session)
    if ai_analysis:
        lines.extend([
            "",
            "🤖 <b>AI Forensic Assessment:</b>",
            f"<i>{ai_analysis}</i>"
        ])

    message_text = "\n".join(lines)
    send_telegram_message(message_text)
    logger.info("Dispatched intrusion report for IP %s", src_ip)


def process_event(event: Dict[str, Any]):
    event_id = event.get("eventid")
    session_id = event.get("session")

    if not session_id:
        return

    if session_id not in sessions:
        src_ip = event.get("src_ip", "Unknown")
        sessions[session_id] = {
            "src_ip": src_ip,
            "geo": get_geoip_info(src_ip),
            "start_time": time.time(),
            "credentials": [],
            "commands": [],
            "downloads": []
        }

    session = sessions[session_id]

    if event_id in ("cowrie.login.success", "cowrie.login.failed"):
        user = event.get("username", "")
        pwd = event.get("password", "")
        session["credentials"].append((user, pwd))

    elif event_id == "cowrie.command.input":
        cmd = event.get("input", "").strip()
        if cmd and cmd not in session["commands"]:
            session["commands"].append(cmd)

    elif event_id == "cowrie.session.file_download":
        url = event.get("url", "") or event.get("outfile", "")
        if url:
            session["downloads"].append(url)

    elif event_id == "cowrie.session.closed":
        dispatch_session_report(session_id)


def tail_cowrie_log():
    """Tails cowrie.json continuously."""
    logger.info("Awaiting Cowrie log file at %s...", LOG_FILE_PATH)
    while not os.path.exists(LOG_FILE_PATH):
        time.sleep(2)

    logger.info("Attached to %s. Monitoring live events...", LOG_FILE_PATH)
    with open(LOG_FILE_PATH, "r") as f:
        # Seek to end of file so we only alert on new events
        f.seek(0, os.SEEK_END)

        while True:
            line = f.readline()
            if not line:
                time.sleep(0.5)
                continue

            try:
                event = json.loads(line.strip())
                process_event(event)
            except Exception as e:
                logger.warning("Error parsing log line: %s", e)


def main():
    logger.info("Cowrie Threat Reporter starting up...")
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning(
            "TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing. Alerts will only be logged."
        )

    try:
        tail_cowrie_log()
    except KeyboardInterrupt:
        logger.info("Reporter shutting down.")


if __name__ == "__main__":
    main()
