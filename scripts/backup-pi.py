#!/usr/bin/env python3
"""
Argon Pi Automated Backup System
- Atomic SQLite snapshots using Python's native SQLite Online Backup API (WAL-safe).
- Integrity checks (PRAGMA integrity_check) on all database copies before archiving.
- Backs up all production .env secrets.
- Packages into compressed .tar.gz archives in syncthing/Sync/backups/.
- Updates a 'latest/' folder for immediate, uncompressed inspection.
- Rotates backups: retains the last 28 snapshots (7 days at 6-hour intervals).
- Alerts via Telegram if a backup ever encounters an error.
"""

import os
import sys
import shutil
import sqlite3
import tarfile
import hashlib
import json
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

# Base directories
BASE_DIR = Path(__file__).resolve().parent.parent
BACKUP_DIR = BASE_DIR / "syncthing" / "Sync" / "backups"
LATEST_DIR = BACKUP_DIR / "latest"
RETENTION_COUNT = 28  # 7 days of 6-hourly backups

# Targets
SQLITE_TARGETS = [
    ("users.db", BASE_DIR / "argon-auth" / "data" / "users.db"),
    ("vaultwarden.db", BASE_DIR / "vaultwarden" / "data" / "db.sqlite3"),
    ("uptime-kuma.db", BASE_DIR / "uptime-kuma" / "data" / "kuma.db"),
    ("beszel.db", BASE_DIR / "beszel" / "beszel_data" / "data.db"),
    ("stock-watcher.db", BASE_DIR / "stock-watcher" / "data" / "seen_articles.db"),
]

ENV_TARGETS = [
    ("argon-auth.env", BASE_DIR / "argon-auth" / ".env"),
    ("stock-watcher.env", BASE_DIR / "stock-watcher" / ".env"),
    ("watchtower.env", BASE_DIR / "watchtower" / ".env"),
]


def send_telegram_alert(message: str):
    """Sends failure alert via Telegram bot if credentials exist in stock-watcher/.env."""
    env_file = BASE_DIR / "stock-watcher" / ".env"
    if not env_file.exists():
        return
    token = None
    chat_id = None
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
                "text": f"⚠️ <b>Argon Pi Backup Alert</b>\n{message}",
                "parse_mode": "HTML"
            }
            data = urllib.parse.urlencode(payload).encode("utf-8")
            req = urllib.request.Request(url, data=data, method="POST")
            urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        print(f"[Backup] Failed to send Telegram notification: {e}", file=sys.stderr)


def backup_sqlite(src_path: Path, dst_path: Path) -> bool:
    """Performs an atomic online SQLite snapshot and verifies its integrity."""
    if not src_path.exists():
        return False
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    if dst_path.exists():
        dst_path.unlink()

    src_conn = sqlite3.connect(f"file:{src_path.resolve()}?mode=ro", uri=True)
    dst_conn = sqlite3.connect(dst_path)
    with dst_conn:
        src_conn.backup(dst_conn)
    dst_conn.close()
    src_conn.close()

    # Integrity verification
    check_conn = sqlite3.connect(dst_path)
    try:
        result = check_conn.execute("PRAGMA integrity_check;").fetchone()
        if not result or result[0] != "ok":
            raise RuntimeError(f"Integrity check failed: {result}")
    finally:
        check_conn.close()
    return True


def run_backup():
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    archive_name = f"argon-backup-{timestamp}.tar.gz"
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(BACKUP_DIR, 0o700)

    staging_dir = BACKUP_DIR / f"_staging_{timestamp}"
    staging_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(staging_dir, 0o700)

    dbs_dir = staging_dir / "dbs"
    envs_dir = staging_dir / "envs"
    dbs_dir.mkdir(parents=True, exist_ok=True)
    envs_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "timestamp": timestamp,
        "date_utc": datetime.now(timezone.utc).isoformat(),
        "hostname": os.uname().nodename,
        "databases": [],
        "env_files": []
    }

    print(f"📦 Starting Argon Pi Backup: {timestamp}")

    # 1. Back up SQLite databases
    for name, path in SQLITE_TARGETS:
        if path.exists():
            target_path = dbs_dir / name
            try:
                backup_sqlite(path, target_path)
                size_kb = round(target_path.stat().st_size / 1024, 2)
                manifest["databases"].append({"name": name, "size_kb": size_kb, "status": "ok"})
                print(f"  ✅ DB backed up: {name} ({size_kb} KB)")
            except Exception as e:
                err_msg = f"Failed to backup {name}: {e}"
                print(f"  ❌ {err_msg}", file=sys.stderr)
                manifest["databases"].append({"name": name, "status": f"error: {e}"})
                send_telegram_alert(err_msg)

    # 2. Back up .env secrets
    for name, path in ENV_TARGETS:
        if path.exists():
            target_path = envs_dir / name
            shutil.copy2(path, target_path)
            os.chmod(target_path, 0o600)
            manifest["env_files"].append(name)
            print(f"  🔒 Secret backed up: {name}")

    # 3. Write manifest
    manifest_path = staging_dir / "manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)

    # 4. Create compressed .tar.gz archive
    archive_path = BACKUP_DIR / archive_name
    with tarfile.open(archive_path, "w:gz") as tar:
        for item in staging_dir.iterdir():
            tar.add(item, arcname=item.name)
    os.chmod(archive_path, 0o600)
    archive_size_kb = round(archive_path.stat().st_size / 1024, 2)
    print(f"🗜️ Archive created: {archive_name} ({archive_size_kb} KB)")

    # 5. Update latest/ directory for immediate access
    if LATEST_DIR.exists():
        shutil.rmtree(LATEST_DIR)
    shutil.copytree(staging_dir, LATEST_DIR)
    os.chmod(LATEST_DIR, 0o700)
    print(f"🔄 Updated latest snapshot at: {LATEST_DIR}")

    # 6. Clean up staging
    shutil.rmtree(staging_dir)

    # 7. Rotate old backups (keep most recent RETENTION_COUNT)
    archives = sorted(BACKUP_DIR.glob("argon-backup-*.tar.gz"))
    if len(archives) > RETENTION_COUNT:
        to_delete = archives[:-RETENTION_COUNT]
        for old_arc in to_delete:
            old_arc.unlink()
            print(f"  🧹 Rotated old backup: {old_arc.name}")

    print(f"✨ Backup completed successfully! Total snapshots retained: {min(len(archives), RETENTION_COUNT)}")


def list_backups():
    if not BACKUP_DIR.exists():
        print("No backups directory found.")
        return
    archives = sorted(BACKUP_DIR.glob("argon-backup-*.tar.gz"))
    if not archives:
        print("No backup archives found.")
        return
    print(f"=== Available Argon Pi Backups ({len(archives)}) ===")
    for a in archives:
        size_kb = round(a.stat().st_size / 1024, 2)
        mtime = datetime.fromtimestamp(a.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        print(f"• {a.name} | {size_kb} KB | {mtime}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--list":
        list_backups()
    else:
        try:
            run_backup()
        except Exception as ex:
            msg = f"Fatal error during backup run: {ex}"
            print(f"❌ {msg}", file=sys.stderr)
            send_telegram_alert(msg)
            sys.exit(1)
