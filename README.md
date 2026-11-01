# My Pi v2.1 — Homelab & Security Ops Suite

A production-ready collection of lightweight security, intelligence, and privacy microservices tailored for the Raspberry Pi (4GB RAM, 32GB microSD), complete with automated self-healing, cloud dead-man's switch, and native mobile companion client.

---

## 🏛️ System Architecture

```mermaid
flowchart TD
    subgraph Client["📱 Client Layer (Argon Mobile Hub)"]
        UI["Jetpack Compose UI\n(Native Hub & HUD)"]
        NM["NetworkManager\n(10s Active Heartbeat)"]
        OTA["OtaManager\n(OTA Auto-Update Client)"]
        AUTH_CLIENT["AuthManager\n(JWT Session & RBAC)"]
        
        UI --> NM
        UI --> OTA
        UI --> AUTH_CLIENT
    end

    subgraph Network["🌐 Transport & Overlay Network"]
        LAN["Home Wi-Fi / LAN\n(192.168.1.10)"]
        TS["Tailscale Mesh VPN\n(100.123.87.122 / argon-host)"]
        STUN["WebRTC / STUN\n(Google & Cloudflare STUN)"]
    end

    NM -->|"Direct LAN (400ms)"| LAN
    NM -->|"WireGuard Tunnel (1200ms)"| TS
    UI -->|"P2P File Transfer"| STUN

    subgraph Server["🍓 Argon Pi Infrastructure (Raspberry Pi 5)"]
        subgraph Ingress["Gateway & Ingress"]
            CADDY["Caddy Reverse Proxy\n(Port 80 & 5050)"]
            AUTH_SRV["Argon Auth Gateway\n(FastAPI • Port 8089)\n/api/health • /api/auth • /api/version"]
        end

        subgraph Containers["Docker Microservices (17 Stacks)"]
            direction TB
            VW["Vaultwarden (Passwords)"]
            SYNC["Syncthing (Multi-Device Sync)"]
            KUMA["Uptime Kuma (Service Monitor)"]
            BESZEL["Beszel (System Metrics)"]
            CROWD["CrowdSec (Intrusion Defense)"]
            COWRIE["Cowrie (SSH Honeypot)"]
            WATCHER["Stock Watcher & AI Summarizer"]
            TOWER["Watchtower (Nightly Container Updates)"]
            OTHER["PairDrop, Stirling-PDF, IT-Tools, etc."]
        end

        subgraph BackgroundJobs["Self-Healing & Cron Daemons"]
            CRON_PING["healthchecks-ping.sh\n(Every 1 min cron)"]
            CRON_BACKUP["backup-pi.sh\n(Every 6 hrs atomic SQLite & .env)"]
            ON_BOOT["on-boot-check.sh\n(@reboot SQLite PRAGMA integrity check)"]
        end
    end

    LAN --> CADDY
    TS --> CADDY
    LAN --> AUTH_SRV
    TS --> AUTH_SRV
    CADDY --> Containers

    subgraph CloudOps["☁️ External Monitoring & Alerting"]
        HC["Healthchecks.io\n(Dead-Man's Switch • 2 min grace)"]
        TG["Telegram Bot API\n(@PiSentinelBot)"]
    end

    CRON_PING -->|"1-min pulse"| HC
    HC -->|"Downtime Webhook"| TG
    CRON_BACKUP -->|"Backup Report"| TG
    ON_BOOT -->|"Integrity Report"| TG
    COWRIE -->|"Threat Alerts"| TG
    WATCHER -->|"Market / News Summaries"| TG

    subgraph DevOps["🚀 CI/CD & Delivery (GitHub Actions)"]
        REPO_PI["my-pi (Main Server Repo)\n• PR Version Gate (VERSION)\n• Deploy to Pi via Tailscale SSH"]
        REPO_APP["my-pi-mobile (Android Repo)\n• PR Version Gate (versionCode + 1)\n• Fast Kotlin Lint Check\n• Release Build & Deploy to :5050 OTA"]
    end

    REPO_PI -->|"SSH Deploy"| Server
    REPO_APP -->|"Deploy APK & version.json"| CADDY
```

> [!NOTE]
> For the in-depth architectural breakdown including sequence diagrams, see [ARCHITECTURE.md](docs/ARCHITECTURE.md).

---

## 🚀 Active Services & Web Dashboards

| Service | Port / Protocol | Web UI Access | Role |
| :--- | :--- | :--- | :--- |
| **Homepage** | `80` (HTTP) | `http://192.168.1.10/` | Unified homelab HUD with real-time stats & service cards |
| **Argon Auth & RBAC** | `8089` (HTTP) | `http://192.168.1.10:8089/admin` | Multi-user identity, Google OIDC & role access management |
| **Sentinel Bot** | Background Worker | Telegram (`@ArgonPiSentinelBot`) | News watcher, Wi-Fi sentry, system ops |
| **AdGuard Home** | `53` (DNS), `3000`/`8080` (HTTP) | `http://192.168.1.10:8080` | Network-wide ad & telemetry sinkhole |
| **Chromium Sandbox** | `3002` (HTTP), `3003` (HTTPS) | `https://192.168.1.10:3003` | Disposable WebRTC cleanroom browser |
| **Uptime Kuma** | `3001` (HTTP) | `http://192.168.1.10:3001` | Service health & latency monitor |
| **Cowrie Honeypot** | `2222` (SSH), `2223` (Telnet) | `ssh -p 2222 root@pi` | Deception honeypot & attack logger |
| **CrowdSec** | `8081` (LAPI) | Background Daemon | Collaborative threat defense & firewall bouncer |
| **Vaultwarden** | `8085` (HTTP), `8443` (HTTPS) | `https://192.168.1.10:8443` | Self-hosted Bitwarden password & secret vault |
| **Stirling-PDF** | `8086` (HTTP) | `http://192.168.1.10:8086` | 100% private in-browser document editor & OCR |
| **Qdrant Vector DB** | `6333` (REST/UI), `6334` (gRPC) | `http://192.168.1.10:6333/dashboard` | High-performance vector database for AI embeddings & RAG |
| **Syncthing** | `8384` (HTTP), `22000` (Sync) | `http://192.168.1.10:8384` | Encrypted P2P file & Obsidian note sync |
| **Docker Registry** | `5000` (API), `5001` (HTTP UI) | `http://192.168.1.10:5001` | Private local container registry & web catalog |
| **Tailscale Node** | WireGuard Mesh | `100.123.87.122` | Subnet router & encrypted exit node |

---

## ⚡ Power Loss, Resilience & Self-Healing Architecture

The Pi is engineered to withstand abrupt power cuts and automatically recover with zero data loss:
* **Automatic Boot Orchestration:** Docker daemon and Tailscale are systemd-enabled (`systemctl enable docker tailscaled`).
* **Container Self-Healing:** Every service runs with `restart: unless-stopped` — all 17 containers launch immediately when power is restored.
* **On-Boot SQLite PRAGMA Integrity Checks:** `@reboot` cron executes `scripts/on-boot-check.py` to verify all 5 SQLite databases (`users.db`, `vaultwarden.db`, `uptime-kuma.db`, `beszel.db`, `stock-watcher.db`). If corruption is found, it automatically hot-swaps the corrupt database with the latest healthy snapshot from Syncthing.
* **6-Hourly Atomic Snapshots:** Background cron runs `scripts/backup-pi.sh` using SQLite `PRAGMA vacuum_into` for zero-lock atomic backups, archived to Syncthing and verified on Telegram.
* **External Dead-Man's Switch (Healthchecks.io):** A 1-minute cron (`scripts/healthchecks-ping.sh`) pings Healthchecks.io. If power or internet drops for > 2 minutes, Healthchecks cloud triggers a direct Telegram alert to `@PiSentinelBot`.
* **Hardware Watchdog:** Broadcom hardware watchdog enabled (`RuntimeWatchdogSec=15s`) to automatically reboot if the kernel ever freezes.

### On-Boot Self-Healing Flow
```mermaid
sequenceDiagram
    autonumber
    actor Power as Power / Kernel Event
    participant Pi as Raspberry Pi 5
    participant BootCheck as on-boot-check.py (@reboot)
    participant DB as SQLite Databases (*.db)
    participant Backup as Syncthing Backup Archive
    participant TG as Telegram Bot (@PiSentinelBot)

    Power->>Pi: Sudden Power Loss / Reboot
    Pi->>BootCheck: Trigger @reboot cron
    BootCheck->>DB: PRAGMA integrity_check on 5 Databases
    
    alt Integrity Check Succeeded (ok)
        BootCheck->>TG: ✅ Pi Boot Clean: 5/5 DBs intact, 17/17 containers running
    else Database Corruption Detected
        BootCheck->>Backup: Locate latest atomic backup (dbs/<name>.db)
        Backup-->>BootCheck: Stream healthy snapshot
        BootCheck->>DB: Hot-swap corrupt database
        BootCheck->>TG: 🚨 Self-Healing Auto-Restored <name>.db from backup!
    end
```

### Cloud Dead-Man's Switch (Healthchecks.io)
```mermaid
sequenceDiagram
    autonumber
    participant Pi as Raspberry Pi 5
    participant HC as Healthchecks.io (Cloud)
    participant TG as Telegram API
    actor User as Telegram Client (@PiSentinelBot)

    loop Every 60 Seconds
        Pi->>HC: curl -fsS https://hc-ping.com/b91e...
        HC-->>HC: Reset Grace Timer (2 minutes)
    end

    Note over Pi: Power outage or network interruption occurs
    HC-->>HC: 60s missed pulse
    HC-->>HC: 120s grace period expired (Trigger DOWN)
    HC->>TG: POST /bot<TOKEN>/sendMessage (Webhook)
    TG->>User: 🚨 [Argon-Pi] DOWN ALERT! Heartbeat missed > 2 min

    Note over Pi: Power restored, Pi boots & crons resume
    Pi->>HC: curl -fsS https://hc-ping.com/b91e...
    HC->>TG: POST /bot<TOKEN>/sendMessage (Webhook)
    TG->>User: ✅ [Argon-Pi] RECOVERED! Pi is back online
```

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

## 🛠️ Management & CI/CD Deployment

### Branch & PR Workflow Convention
1. Create a feature branch from `main`:
   ```bash
   git checkout -b feat/your-feature
   ```
2. Make changes, commit, and push to GitHub:
   ```bash
   git push -u origin feat/your-feature
   ```
3. Open a Pull Request to `main`. GitHub Actions will automatically run linting and validate all Docker Compose syntax.
4. On merging the PR into `main`, GitHub Actions automatically connects over Tailscale and deploys to the Raspberry Pi!

### Local Deployment
You can also run deployment manually from Mac or directly on the Pi:
- **From Mac:**
  ```bash
  ./deploy.sh
  ```
- **Directly on the Pi:**
  ```bash
  ./scripts/deploy-local.sh
  ```

### Required GitHub Secrets (Repository Settings -> Secrets -> Actions)
* `TAILSCALE_AUTHKEY` — Tailscale Auth Key (generated from Tailscale Admin Console)
* `PI_HOST` — Tailscale IP of the Pi (`100.123.87.122`)
* `PI_USER` — `argon`
* `PI_SSH_KEY` — Private SSH key authorized in `/home/argon/.ssh/authorized_keys`
