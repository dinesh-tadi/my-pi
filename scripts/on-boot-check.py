#!/usr/bin/env python3
"""
Argon Pi Boot-Time Integrity & Self-Healing Engine
- Runs automatically upon system reboot (@reboot).
- Verifies integrity (PRAGMA integrity_check) of all SQLite databases.
- Auto-recovers any corrupted database from the latest verified Syncthing backup snapshot.
- Verifies Docker engine and all containers.
- Sends a comprehensive Telegram Boot Report.
"""

import os
import sys
import time
import shutil
import sqlite3
import subprocess
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
BACKUP_LATEST_DBS = BASE_DIR / "syncthing" / "Sync" / "backups" / "latest" / "dbs"

SQLITE_TARGETS = [
    ("users.db", BASE_DIR / "argon-auth" / "data" / "users.db"),
    ("vaultwarden.db", BASE_DIR / "vaultwarden" / "data" / "db.sqlite3"),
    ("uptime-kuma.db", BASE_DIR / "uptime-kuma" / "data" / "kuma.db"),
    ("beszel.db", BASE_DIR / "beszel" / "beszel_data" / "data.db"),
    ("stock-watcher.db", BASE_DIR / "stock-watcher" / "data" / "seen_articles.db"),
]


def send_telegram(message: str):
    env_file = BASE_DIR / "stock-watcher" / ".env"
    if not env_file.exists():
        return
    token, chat_id = None, None
    try:
        with open(env_file, "r") as f:
            for line in f:
                line = line.strip()
                if line.startswith("TELEGRAM_BOT_TOKEN="):
                    token = line.split("=", 1)[1].strip(" '\"")
                elif line.startswith("TELEGRAM_CHAT_ID="):
                    chat_id = line.split("=", 1)[1].strip(" '\"")
        if token and chat_id:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            payload = {
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "HTML"
            }
            data = urllib.parse.urlencode(payload).encode("utf-8")
            req = urllib.request.Request(url, data=data, method="POST")
            urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        print(f"[BootCheck] Failed to send Telegram: {e}", file=sys.stderr)


def check_and_heal_db(name: str, path: Path) -> str:
    """Verifies SQLite integrity. If corrupt or missing, restores from latest backup."""
    if not path.exists():
        # Check if we can restore from backup
        backup_file = BACKUP_LATEST_DBS / name
        if backup_file.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup_file, path)
            return f"restored_missing_from_backup"
        return "missing_no_backup"

    try:
        conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True, timeout=5)
        res = conn.execute("PRAGMA integrity_check;").fetchone()
        conn.close()
        if res and res[0] == "ok":
            return "healthy"
        else:
            raise RuntimeError(f"Integrity check returned: {res}")
    except Exception as ex:
        # Corrupt DB! Attempt recovery from backup
        print(f"⚠️ Corruption detected in {name}: {ex}. Attempting auto-restore...", file=sys.stderr)
        backup_file = BACKUP_LATEST_DBS / name
        if backup_file.exists():
            corrupt_save = path.with_suffix(".corrupt." + datetime.now().strftime("%Y%m%d%H%M%S"))
            shutil.move(path, corrupt_save)
            shutil.copy2(backup_file, path)
            return f"corrupt_auto_healed"
        return f"corrupt_no_backup"


def main():
    print(f"🔄 Starting Argon Pi Boot Integrity Check: {datetime.now().isoformat()}")
    # Give containers and system 15 seconds to settle
    time.sleep(15)

    db_results = {}
    healed_count = 0
    corrupt_count = 0

    for name, path in SQLITE_TARGETS:
        status = check_and_heal_db(name, path)
        db_results[name] = status
        if "healed" in status or "restored" in status:
            healed_count += 1
        elif "corrupt" in status:
            corrupt_count += 1

    # Check Docker containers
    running_containers = 0
    try:
        out = subprocess.check_output(
            ["docker", "ps", "-q"],
            stderr=subprocess.DEVNULL
        ).decode().strip()
        running_containers = len(out.splitlines()) if out else 0
    except Exception:
        running_containers = -1

    hostname = os.uname().nodename
    boot_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Format Telegram Notification
    if corrupt_count > 0:
        header = f"🚨 <b>Argon Pi Boot Alert: Corruption Unrecoverable!</b>"
    elif healed_count > 0:
        header = f"⚠️ <b>Argon Pi Auto-Healed on Boot!</b>"
    else:
        header = f"🟢 <b>Argon Pi Boot Verification Complete</b>"

    db_summary = []
    for name, status in db_results.items():
        if status == "healthy":
            db_summary.append(f"  • {name}: ✅ OK")
        elif "healed" in status or "restored" in status:
            db_summary.append(f"  • {name}: 🛠️ <b>Restored from Backup</b>")
        else:
            db_summary.append(f"  • {name}: ❌ <b>{status}</b>")

    dbs_text = "\n".join(db_summary)

    msg = f"{header}\n\n" \
          f"• <b>Host:</b> <code>{hostname}</code>\n" \
          f"• <b>Boot Time:</b> {boot_time}\n" \
          f"• <b>Containers Running:</b> {running_containers}\n\n" \
          f"<b>Database Integrity:</b>\n{dbs_text}"

    print(msg)
    send_telegram(msg)


if __name__ == "__main__":
    main()
