# My Pi — Homelab Projects

A production-ready collection of lightweight security, intelligence, and utility services tailored for the Raspberry Pi 4 (4GB RAM, 32GB microSD).

## Projects

1. **`stock-watcher/`**
   - 24/7 background AI stock, earnings, & cybersecurity news monitor.
   - Polls Yahoo Finance, Google News, Hacker News, and BleepingComputer.
   - De-duplicates articles in SQLite and calls Gemini Flash for 2-line financial/security impact assessments.
   - Sends real-time HTML alerts to your Telegram bot.

2. **`cowrie-honeypot/`**
   - Dockerized Cowrie SSH & Telnet honeypot listening on port `2222` (safe from real SSH on port `22`).
   - Automated Threat Reporter that tails `cowrie.json`, performs GeoIP lookups, generates AI attack forensics via Gemini, and sends live intrusion alerts to your Telegram bot.

---

## Quick Setup Guide

### Step 1: Telegram Bot Setup (Takes 60 seconds)
1. In Telegram, search for **`@BotFather`** and send `/newbot`.
2. Follow the prompt to name your bot and choose a username (e.g. `dinesh_sentinel_bot`).
3. Copy the **HTTP API Token** provided by BotFather.
4. Search for **`@userinfobot`** in Telegram, press Start, and note your numeric **Id**.
5. **Important:** Send `/start` or any greeting to your new bot so it has permission to message you.

### Step 2: Configure Environment
Copy and edit the `.env` file in `stock-watcher/`:
```bash
cp stock-watcher/.env.example stock-watcher/.env
nano stock-watcher/.env
```
Fill in:
```ini
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHI...
TELEGRAM_CHAT_ID=987654321
GEMINI_API_KEY=AIzaSy...   # Optional: for AI analysis
```

### Step 3: One-Click Deploy to Raspberry Pi
Run the deploy script from your Mac:
```bash
./deploy.sh
```
This syncs code, builds containers on the Pi, and starts the services.

### Testing the Honeypot
From another terminal on your Mac, test connecting to the fake SSH shell:
```bash
ssh -p 2222 root@pi
```
(Try any password, run `uname -a`, `cat /etc/passwd`). You will see an instant intrusion report land in your Telegram!
