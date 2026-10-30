#!/usr/bin/env bash
set -euo pipefail

# Script: deploy-local.sh
# Purpose: Execute local deployment directly on Raspberry Pi (called by GitHub Actions or local CLI)

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$BASE_DIR"

notify_failure() {
    local lineno="$1"
    if [ -f "stock-watcher/.env" ]; then
        BOT_TOKEN=$(grep -E '^TELEGRAM_BOT_TOKEN=' stock-watcher/.env | cut -d '=' -f2- | tr -d ' "')
        CHAT_ID=$(grep -E '^TELEGRAM_CHAT_ID=' stock-watcher/.env | cut -d '=' -f2- | tr -d ' "')
        if [ -n "$BOT_TOKEN" ] && [ -n "$CHAT_ID" ]; then
            COMMIT_SHORT=$(git rev-parse --short HEAD 2>/dev/null || echo "latest")
            curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
                -d chat_id="${CHAT_ID}" \
                -d parse_mode="HTML" \
                -d text="❌ <b>My Pi Deployment FAILED!</b>%0A• <b>Failed at line:</b> ${lineno}%0A• <b>Commit:</b> <code>${COMMIT_SHORT}</code>%0A• <b>Host:</b> $(hostname)" >/dev/null 2>&1 || true
        fi
    fi
}
trap 'notify_failure $LINENO' ERR

echo "=========================================================="
echo "🚀 Deploying My Pi Stack on $(hostname): $(date)"
echo "Directory: $BASE_DIR"
echo "=========================================================="

# 1. Pull latest commits from GitHub
echo "📥 Syncing latest commits from origin/main..."
if [ -d ".git" ]; then
    git fetch origin main
    git reset --hard origin/main
    echo "✅ Successfully updated code to latest commit: $(git rev-parse --short HEAD)"
else
    echo "⚠️ Not a git repository, skipping git pull."
fi

# 2. Ensure data and config directories exist
echo "📁 Ensuring required persistent directories exist..."
mkdir -p cowrie-honeypot/cowrie-data/{logs,downloads,keys}
mkdir -p uptime-kuma/data
mkdir -p adguard-home/{work,conf}
mkdir -p disposable-browser/config
mkdir -p vaultwarden/{data,caddy_data}
mkdir -p stirling-pdf/{trainingData,extraConfigs,customFiles,logs}
mkdir -p syncthing/{config,Sync/backups}
mkdir -p docker-registry/data
mkdir -p homepage/config
mkdir -p qdrant/storage
mkdir -p crowdsec/{config,data}
mkdir -p argon-ota/dist
mkdir -p argon-auth/data

# Ensure backup cron job (every 6 hours)
chmod +x scripts/backup-pi.sh scripts/backup-pi.py
(crontab -l 2>/dev/null | grep -q 'backup-pi.sh') || (
    (crontab -l 2>/dev/null; echo '0 */6 * * * /home/argon/american-pi/scripts/backup-pi.sh >> /home/argon/american-pi/syncthing/Sync/backups/backup.log 2>&1') | crontab -
) || true

# Ensure honeypot permissions if required
[ -d cowrie-honeypot/cowrie-data ] && chmod -R 777 cowrie-honeypot/cowrie-data 2>/dev/null || true

# 3. Update custom-built containers if code changed
echo "🔨 Building custom microservices if updated..."
# Release port 80 from homepage if transitioning to port 8082
if [ -d "homepage" ] && [ -f "homepage/docker-compose.yml" ]; then
    (cd homepage && docker compose up -d) || true
fi

if [ -d "argon-auth" ] && [ -f "argon-auth/docker-compose.yml" ]; then
    echo "➡️ Building & starting argon-auth..."
    [ ! -f "argon-auth/.env" ] && [ -f "argon-auth/.env.example" ] && cp argon-auth/.env.example argon-auth/.env || true
    (cd argon-auth && docker compose up -d --build)
fi

if [ -d "stock-watcher" ] && [ -f "stock-watcher/docker-compose.yml" ]; then
    echo "➡️ Building & starting stock-watcher..."
    (cd stock-watcher && docker compose up -d --build)
fi

if [ -d "cowrie-honeypot" ] && [ -f "cowrie-honeypot/docker-compose.yml" ]; then
    echo "➡️ Building & starting cowrie-threat-reporter..."
    (cd cowrie-honeypot && docker compose up -d --build)
fi

# 4. Update standard services
services=(
    "uptime-kuma"
    "adguard-home"
    "disposable-browser"
    "vaultwarden"
    "stirling-pdf"
    "syncthing"
    "docker-registry"
    "homepage"
    "qdrant"
    "crowdsec"
    "it-tools"
    "beszel"
    "watchtower"
    "argon-ota"
    "pairdrop"
)

for svc in "${services[@]}"; do
    if [ -d "$svc" ] && [ -f "$svc/docker-compose.yml" ]; then
        echo "➡️ Starting $svc..."
        (cd "$svc" && docker compose up -d --remove-orphans)
    fi
done

# 5. Clean up dangling images to preserve microSD storage
echo "🧹 Pruning unused dangling images..."
docker image prune -f >/dev/null 2>&1 || true

echo "=========================================================="
echo "🎉 Deployment Complete! Live Containers on your Pi:"
echo "=========================================================="
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"

# 6. Send Telegram Notification
if [ -f "stock-watcher/.env" ]; then
    BOT_TOKEN=$(grep -E '^TELEGRAM_BOT_TOKEN=' stock-watcher/.env | cut -d '=' -f2- | tr -d ' "')
    CHAT_ID=$(grep -E '^TELEGRAM_CHAT_ID=' stock-watcher/.env | cut -d '=' -f2- | tr -d ' "')

    if [ -n "$BOT_TOKEN" ] && [ -n "$CHAT_ID" ]; then
        COMMIT_SHORT=$(git rev-parse --short HEAD 2>/dev/null || echo "latest")
        COMMIT_MSG=$(git log -1 --pretty=%s 2>/dev/null || echo "Manual deployment")
        ACTIVE_COUNT=$(docker ps -q | wc -l | tr -d ' ')
        TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

        MSG="🚀 <b>My Pi Deployment Successful!</b>%0A• <b>Commit:</b> <code>${COMMIT_SHORT}</code>: ${COMMIT_MSG}%0A• <b>Containers Running:</b> ${ACTIVE_COUNT}%0A• <b>Host:</b> $(hostname)%0A• <b>Time:</b> ${TIMESTAMP}"

        echo "📲 Sending Telegram deployment notification..."
        curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
            -d chat_id="${CHAT_ID}" \
            -d parse_mode="HTML" \
            -d text="${MSG}" >/dev/null 2>&1 || true
        echo "✅ Telegram notification dispatched."
    fi
fi
