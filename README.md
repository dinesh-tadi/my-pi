# My Pi — Homelab & Security Ops Suite

A production-ready collection of lightweight security, intelligence, and privacy microservices tailored for the Raspberry Pi 4 (4GB RAM, 32GB microSD).

## 🚀 Active Services & Web Dashboards

| Service | Port / Protocol | Web UI Access | Role |
| :--- | :--- | :--- | :--- |
| **Sentinel Bot** | Background Worker | Telegram (`@ArgonPiSentinelBot`) | News watcher, Wi-Fi sentry, system ops |
| **AdGuard Home** | `53` (DNS), `3000`/`8080` (HTTP) | `http://192.168.1.10:3000` | Network-wide ad & telemetry sinkhole |
| **Chromium Sandbox** | `3002` (HTTP), `3003` (HTTPS) | `http://192.168.1.10:3002` | Disposable WebRTC cleanroom browser |
| **Uptime Kuma** | `3001` (HTTP) | `http://192.168.1.10:3001` | Service health & latency monitor |
| **Cowrie Honeypot** | `2222` (SSH), `2223` (Telnet) | `ssh -p 2222 root@pi` | Deception honeypot & attack logger |
| **Tailscale Node** | WireGuard Mesh | `100.123.87.122` | Subnet router & encrypted exit node |

---

## 📱 Telegram Bot Commands

Send any of these commands to **Pi Sentinel** in Telegram:

### Hardware & Network
* `/pi` — Real-time CPU temp (°C), RAM, Disk, Load, and Docker containers
* `/devices` — Lists active Wi-Fi/LAN devices, hardware vendor, and connection time in IST
* `/ping` — Measures network latency to Cloudflare (`1.1.1.1`) and Google (`8.8.8.8`)

### Security & Developer Tools
* `/scan <url>` — Headless redirect chain tracker and payload risk inspector
* `/web <url>` — Fetches any article/blog and creates a clean executive summary
* `/cve <package>` — Instant vulnerability search across OSV and NVD
* `/trending` — Top trending open-source AI/LLM GitHub repositories

### Feed & Watchlist Control
* `/interval <mins>` — Change feed check frequency (e.g. `/interval 30`)
* `/age <hours>` — Set publication freshness cutoff (e.g. `/age 24`)
* `/check` — Run an immediate manual feed check
* `/status` — View current bot configuration and database metrics
* `/list` — Display active stock/crypto/security keywords
* `/add <word>` / `/remove <word>` — Modify watchlist tickers dynamically

---

## 🛠️ Management & Deployment

Deploy or update all services from your Mac:
```bash
./deploy.sh
```
