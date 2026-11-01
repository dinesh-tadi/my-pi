# Argon Pi Ecosystem Architecture Specification

This document provides a comprehensive architectural specification of the **Argon Pi** home server ecosystem, including network topology, container stacks, self-healing engines, external dead-man's switch, and CI/CD pipelines.

---

## 1. End-to-End System Topology

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

---

## 2. Server-Side Resilience & Self-Healing Sequence

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

---

## 3. External Dead-Man's Switch Heartbeat

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

## 4. CI/CD Deployment Architecture

```mermaid
flowchart LR
    subgraph GitHub["GitHub Actions CI/CD"]
        PR["Pull Request to main"] --> GATE["PR Gate:\n• Root VERSION Upgrade Check\n• Shell Syntax (bash -n)\n• Docker Compose Syntax"]
        GATE -->|"Pass"| MERGE["Merge into main"]
        MERGE --> DEPLOY["CD Pipeline:\n• Connect to Tailscale WireGuard\n• SSH into Pi\n• scripts/deploy-local.sh"]
    end

    subgraph Pi["Raspberry Pi 5"]
        DEPLOY --> PULL["git pull origin main"]
        PULL --> RELOAD["Reload Systemd Services &\nDocker Containers"]
    end
```
