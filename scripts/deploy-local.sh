#!/usr/bin/env bash
set -euo pipefail

# Script: deploy-local.sh
# Purpose: Execute local deployment directly on Raspberry Pi (called by GitHub Actions or local CLI)

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$BASE_DIR"

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
mkdir -p syncthing/{config,Sync}
mkdir -p docker-registry/data
mkdir -p homepage/config
mkdir -p qdrant/storage
mkdir -p crowdsec/{config,data}
mkdir -p argon-ota/dist

# Ensure honeypot permissions if required
[ -d cowrie-honeypot/cowrie-data ] && chmod -R 777 cowrie-honeypot/cowrie-data 2>/dev/null || true

# 3. Update custom-built containers if code changed
echo "🔨 Building custom microservices if updated..."
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
