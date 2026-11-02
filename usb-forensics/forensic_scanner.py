#!/usr/bin/env python3
"""
USB Forensics & Quarantine Bay
Detects newly attached USB storage devices, automatically mounts them in
read-only/noexec sandbox, indexes files, computes SHA-256 hashes, detects
malware/exploit indicators (autorun, double extensions, LNK shortcuts),
evaluates threat risk via Gemini 2.5 Flash, and dispatches incident dossiers to Telegram.
"""

import os
import sys
import time
import hashlib
import subprocess
import logging
import asyncio
from typing import Dict, List, Any, Optional
import requests
from dotenv import load_dotenv
from fastapi import FastAPI
import uvicorn

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("USBForensics")

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
QUARANTINE_BASE = os.getenv("QUARANTINE_BASE", "/media/quarantine")
POLL_INTERVAL_SECS = int(os.getenv("POLL_INTERVAL_SECS", "5"))
METRICS_PORT = int(os.getenv("METRICS_PORT", "8094"))

SUSPICIOUS_EXTENSIONS = {
    ".exe", ".scr", ".com", ".pif", ".bat", ".cmd", ".vbs", ".vbe",
    ".js", ".jse", ".wsf", ".wsh", ".ps1", ".ps1xml", ".ps2", ".psc1",
    ".sh", ".bash", ".elf", ".bin", ".dll", ".sys", ".drv", ".cpl",
    ".lnk", ".inf", ".url", ".docm", ".xlsm", ".pptm"
}

scanned_partitions = set()
recent_scans: List[Dict[str, Any]] = []


def send_telegram_alert(text: str):
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
        requests.post(url, json=payload, timeout=8)
    except Exception as e:
        logger.error("Failed sending Telegram alert: %s", e)


def compute_sha256(filepath: str, max_bytes: int = 20 * 1024 * 1024) -> str:
    """Computes SHA-256 for a file (capped to max_bytes for speed)."""
    h = hashlib.sha256()
    try:
        with open(filepath, "rb") as f:
            bytes_read = 0
            while chunk := f.read(65536):
                h.update(chunk)
                bytes_read += len(chunk)
                if bytes_read >= max_bytes:
                    break
        return h.hexdigest()
    except Exception:
        return "READ_ERROR"


def analyze_with_gemini(flagged_files: List[Dict[str, Any]], dev_info: Dict[str, Any]) -> str:
    """Invokes Gemini 2.5 Flash to assess malware risk of flagged files."""
    if not GEMINI_API_KEY or not flagged_files:
        return "No suspicious payloads flagged."

    prompt = (
        "You are an expert digital forensics examiner analyzing an unknown USB drive insertion.\n"
        f"Device: {dev_info.get('device')} | Total files: {dev_info.get('total_files')}\n"
        f"Suspicious files detected on USB drive:\n"
    )
    for f in flagged_files[:10]:
        prompt += f"- {f['name']} ({f['size_kb']} KB, ext: {f['ext']}) SHA256: {f['sha256'][:16]}...\n"

    prompt += (
        "\nProvide a concise 2-sentence forensic risk verdict:\n"
        "1. Likely payload type (e.g. BadUSB dropper, credential stealer, autorun worm, benign admin tools)\n"
        "2. Risk rating: [SAFE / SUSPICIOUS / MALICIOUS] and whether it is safe to open on host systems."
    )

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent?key={GEMINI_API_KEY}"
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
        logger.warning("Gemini USB analysis error: %s", e)

    return "Risk Evaluation Unavailable (Gemini offline)"


def scan_usb_partition(dev_path: str):
    """Mounts partition strictly read-only, audits file tree, hashes files, and alerts."""
    part_name = os.path.basename(dev_path)
    mount_dir = os.path.join(QUARANTINE_BASE, f"quarantine_{part_name}_{int(time.time())}")
    os.makedirs(mount_dir, exist_ok=True)

    logger.info("Quarantine mounting %s to %s (ro,nosuid,nodev,noexec)...", dev_path, mount_dir)
    mount_cmd = ["mount", "-o", "ro,nosuid,nodev,noexec", dev_path, mount_dir]
    res = subprocess.run(mount_cmd, capture_output=True, text=True)

    if res.returncode != 0:
        logger.error("Failed to mount %s: %s", dev_path, res.stderr)
        return

    try:
        total_files = 0
        total_size = 0
        flagged_files = []

        for root, dirs, files in os.walk(mount_dir):
            for file in files:
                total_files += 1
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, mount_dir)

                try:
                    st = os.stat(full_path)
                    fsize = st.st_size
                    total_size += fsize
                except Exception:
                    fsize = 0

                name_lower = file.lower()
                _, ext = os.path.splitext(name_lower)

                is_flagged = False
                reason = []

                if ext in SUSPICIOUS_EXTENSIONS:
                    is_flagged = True
                    reason.append(f"Executable/Script Extension ({ext})")

                if name_lower == "autorun.inf":
                    is_flagged = True
                    reason.append("Autorun Config Detected")

                if name_lower.count(".") > 1:
                    is_flagged = True
                    reason.append("Double File Extension")

                if is_flagged:
                    sha = compute_sha256(full_path)
                    flagged_files.append({
                        "name": rel_path,
                        "size_kb": round(fsize / 1024, 1),
                        "ext": ext,
                        "sha256": sha,
                        "reasons": reason
                    })

        dev_info = {
            "device": dev_path,
            "total_files": total_files,
            "total_mb": round(total_size / (1024 * 1024), 2),
            "flagged_count": len(flagged_files),
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
        }

        # Run AI Forensic Assessment if any suspicious files found
        ai_verdict = analyze_with_gemini(flagged_files, dev_info) if flagged_files else "Drive appears clean of high-risk executables."

        # Compile and dispatch Telegram Dossier
        lines = [
            "🔌 <b>USB Forensics Bay: New Storage Attached!</b>",
            f"💾 <b>Partition:</b> <code>{dev_path}</code> (Mounted Read-Only)",
            f"📁 <b>Total Files:</b> <code>{total_files}</code> ({dev_info['total_mb']} MB)",
            f"🚩 <b>Suspicious Indicators:</b> <code>{len(flagged_files)} flagged</code>",
            ""
        ]

        if flagged_files:
            lines.append("⚠️ <b>Flagged Files:</b>")
            for f in flagged_files[:5]:
                lines.append(f" • <code>{f['name']}</code> ({f['size_kb']} KB) [<i>{', '.join(f['reasons'])}</i>]")
            if len(flagged_files) > 5:
                lines.append(f" • <i>...and {len(flagged_files) - 5} more flagged</i>")
            lines.append("")

        lines.extend([
            "🤖 <b>Forensic AI Verdict:</b>",
            f"<i>{ai_verdict}</i>"
        ])

        send_telegram_alert("\n".join(lines))
        recent_scans.append(dev_info)

    finally:
        # Safely unmount quarantine
        logger.info("Unmounting quarantine %s...", mount_dir)
        subprocess.run(["umount", mount_dir], capture_output=True)
        try:
            os.rmdir(mount_dir)
        except Exception:
            pass


def get_available_partitions() -> List[str]:
    """Finds attached USB block partitions like sdb1, sdc1 (excluding root mmcblk0)."""
    partitions = []
    if os.path.exists("/proc/partitions"):
        with open("/proc/partitions", "r") as f:
            for line in f.readlines()[2:]:
                parts = line.strip().split()
                if len(parts) >= 4:
                    dev_name = parts[3]
                    # Look for sdX partitions (e.g. sdb1, sdc1)
                    if dev_name.startswith("sd") and any(c.isdigit() for c in dev_name):
                        full_dev = f"/dev/{dev_name}"
                        if os.path.exists(full_dev):
                            partitions.append(full_dev)
    return partitions


async def monitor_loop():
    logger.info("USB Forensics Bay monitoring active for external drives...")
    os.makedirs(QUARANTINE_BASE, exist_ok=True)

    while True:
        try:
            current_partitions = set(get_available_partitions())
            new_partitions = current_partitions - scanned_partitions

            for part in new_partitions:
                logger.info("New USB partition detected: %s", part)
                scanned_partitions.add(part)
                scan_usb_partition(part)

            # Clean up disconnected partitions
            for part in list(scanned_partitions):
                if part not in current_partitions:
                    logger.info("USB partition disconnected: %s", part)
                    scanned_partitions.remove(part)

        except Exception as e:
            logger.error("Monitor loop error: %s", e)

        await asyncio.sleep(POLL_INTERVAL_SECS)


app = FastAPI(title="USB Forensics & Quarantine Bay", version="2.2.0")


@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "service": "usb-forensics",
        "attached_partitions": list(scanned_partitions),
        "total_scans": len(recent_scans)
    }


@app.get("/scans")
async def get_scans():
    return {
        "recent_scans": recent_scans,
        "active_devices": list(scanned_partitions)
    }


async def main():
    logger.info("USB Forensics Bay service running on port %s", METRICS_PORT)
    config = uvicorn.Config(app=app, host="0.0.0.0", port=METRICS_PORT, log_level="warning")
    server = uvicorn.Server(config)

    await asyncio.gather(
        server.serve(),
        monitor_loop()
    )


if __name__ == "__main__":
    asyncio.run(main())
