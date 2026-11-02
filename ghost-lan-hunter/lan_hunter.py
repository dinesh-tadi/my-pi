#!/usr/bin/env python3
"""
Ghost-in-the-LAN Sentinel
Monitors home LAN (192.168.1.0/24) for rogue or unknown devices.
Equipped with IEEE 802 Locally Administered Address (LAA) bit detection and
Hostname/mDNS correlation to prevent false-alarm alerts when modern iPhones,
Androids, and MacBooks rotate randomized private Wi-Fi MACs.
"""

import os
import sys
import time
import json
import socket
import logging
from typing import Dict, Any, Optional
import requests
from dotenv import load_dotenv
from fastapi import FastAPI
import uvicorn
from scapy.all import ARP, Ether, srp

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("GhostLANHunter")

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
SUBNET = os.getenv("SUBNET", "192.168.1.0/24")
SCAN_INTERVAL_SECS = int(os.getenv("SCAN_INTERVAL_SECS", "90"))
DATA_DIR = os.getenv("DATA_DIR", "/app/data")
REGISTRY_FILE = os.path.join(DATA_DIR, "known_devices.json")
METRICS_PORT = int(os.getenv("METRICS_PORT", "8093"))

# Device registry: mac -> dict
registry: Dict[str, Any] = {
    "devices": {},
    "learning_mode_until": time.time() + (24 * 3600),  # 24 hour initial baseline learning
    "stats": {"scans_run": 0, "alerts_sent": 0, "mac_rotations_suppressed": 0}
}


def load_registry():
    global registry
    os.makedirs(DATA_DIR, exist_ok=True)
    if os.path.exists(REGISTRY_FILE):
        try:
            with open(REGISTRY_FILE, "r") as f:
                saved = json.load(f)
                registry["devices"] = saved.get("devices", {})
                registry["learning_mode_until"] = saved.get("learning_mode_until", time.time() + 86400)
                registry["stats"] = saved.get("stats", registry["stats"])
                logger.info("Loaded %s known device entries from registry.", len(registry["devices"]))
        except Exception as e:
            logger.error("Failed to load device registry: %s", e)


def save_registry():
    try:
        with open(REGISTRY_FILE, "w") as f:
            json.dump(registry, f, indent=2)
    except Exception as e:
        logger.error("Failed to save device registry: %s", e)


def is_randomized_mac(mac: str) -> bool:
    """Returns True if the MAC address has the Locally Administered Address (LAA) bit set."""
    try:
        first_byte = int(mac.split(":")[0], 16)
        return bool(first_byte & 0x02)
    except Exception:
        return False


def resolve_hostname(ip: str) -> str:
    """Attempts to resolve the device hostname via reverse DNS or mDNS."""
    try:
        host, _, _ = socket.gethostbyaddr(ip)
        return host
    except Exception:
        return "Unknown"


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


def scan_network():
    """Performs an ARP sweep across the subnet."""
    registry["stats"]["scans_run"] += 1
    logger.debug("Running ARP scan on %s...", SUBNET)

    try:
        arp_req = Ether(dst="ff:ff:ff:ff:ff:ff") / ARP(pdst=SUBNET)
        ans, _ = srp(arp_req, timeout=3, verbose=False, iface=None)
    except Exception as e:
        logger.warning("ARP sweep exception: %s", e)
        return

    now = time.strftime("%Y-%m-%d %H:%M:%S")
    is_learning = time.time() < registry["learning_mode_until"]

    for sent, received in ans:
        ip = received.psrc
        mac = received.hwsrc.lower()
        randomized = is_randomized_mac(mac)
        mac_type = "Randomized (Private)" if randomized else "Hardware Vendor OUI"

        # Check if already in registry by MAC
        if mac in registry["devices"]:
            dev = registry["devices"][mac]
            dev["last_seen"] = now
            dev["ip"] = ip
            continue

        # New MAC detected! Attempt hostname resolution
        hostname = resolve_hostname(ip)

        # Check if this hostname is already known under a different randomized MAC
        matched_device_name = None
        for known_mac, dev_info in registry["devices"].items():
            known_host = dev_info.get("hostname", "")
            if hostname != "Unknown" and known_host == hostname:
                matched_device_name = known_host
                break

        if matched_device_name and randomized:
            # An existing known device simply refreshed its randomized private MAC!
            logger.info(
                "Known device '%s' refreshed randomized MAC to %s (Alert suppressed).",
                matched_device_name, mac
            )
            registry["devices"][mac] = {
                "ip": ip,
                "hostname": hostname,
                "mac_type": mac_type,
                "linked_to": matched_device_name,
                "first_seen": now,
                "last_seen": now
            }
            registry["stats"]["mac_rotations_suppressed"] += 1
            save_registry()
            continue

        # Register new device
        registry["devices"][mac] = {
            "ip": ip,
            "hostname": hostname,
            "mac_type": mac_type,
            "first_seen": now,
            "last_seen": now
        }
        save_registry()

        if is_learning:
            logger.info("[Learning Mode] Baseline device registered: IP %s | MAC %s | Host: %s", ip, mac, hostname)
        else:
            # Active Sentinel Mode: Genuine uncataloged device detected!
            logger.warning("🚨 UNKNOWN DEVICE DETECTED: IP %s | MAC %s (%s) | Host: %s", ip, mac, mac_type, hostname)
            registry["stats"]["alerts_sent"] += 1
            send_telegram_alert(
                f"🚨 <b>Ghost-in-the-LAN: Rogue Device Alert!</b>\n"
                f"🌐 <b>IP Address:</b> <code>{ip}</code>\n"
                f"🏷️ <b>MAC Address:</b> <code>{mac}</code>\n"
                f"🛡️ <b>MAC Profile:</b> <i>{mac_type}</i>\n"
                f"📱 <b>Identified Hostname:</b> <code>{hostname}</code>\n"
                f"⏱️ <b>Detected At:</b> <code>{now}</code>\n\n"
                f"<i>Device was not recognized in baseline inventory.</i>"
            )


app = FastAPI(title="Ghost-in-the-LAN Sentinel", version="2.2.0")


@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "service": "ghost-lan-hunter",
        "scans_run": registry["stats"]["scans_run"],
        "known_devices": len(registry["devices"]),
        "learning_mode": time.time() < registry["learning_mode_until"]
    }


@app.get("/devices")
async def get_devices():
    return {
        "total_known_devices": len(registry["devices"]),
        "learning_mode_active": time.time() < registry["learning_mode_until"],
        "devices": registry["devices"],
        "stats": registry["stats"]
    }


async def background_scanner_loop():
    while True:
        try:
            scan_network()
        except Exception as e:
            logger.error("Scan error: %s", e)
        await asyncio.sleep(SCAN_INTERVAL_SECS)


async def main():
    load_registry()
    logger.info("Ghost-in-the-LAN Sentinel starting on subnet %s (Metrics Port: %s)", SUBNET, METRICS_PORT)
    config = uvicorn.Config(app=app, host="0.0.0.0", port=METRICS_PORT, log_level="warning")
    server = uvicorn.Server(config)

    await asyncio.gather(
        server.serve(),
        background_scanner_loop()
    )


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
