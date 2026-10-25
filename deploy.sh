#!/usr/bin/env bash
set -e

PI_HOST="pi"
REMOTE_DIR="~/my-pi"

echo "=========================================================="
echo "🚀 Deploying American-Pi Full Ops Suite to Raspberry Pi"
echo "=========================================================="

# 1. Test SSH connectivity
echo "📡 Checking SSH connection to $PI_HOST..."
if ! ssh -o ConnectTimeout=5 -o BatchMode=yes "$PI_HOST" "echo 'Connected successfully'" >/dev/null 2>&1; then
    echo "❌ Cannot connect to '$PI_HOST'. Check your network and ~/.ssh/config"
    exit 1
fi
echo "✅ SSH connection verified."

# 2. Sync project files to Pi
echo "📦 Syncing project files to $PI_HOST:$REMOTE_DIR..."
ssh "$PI_HOST" "mkdir -p $REMOTE_DIR"
rsync -avz --exclude '.git' --exclude '__pycache__' --exclude '*.db' --exclude 'data' --exclude 'caddy_data' --exclude 'cowrie-data' --exclude 'work' --exclude 'conf' --exclude 'config' --exclude 'Sync' --exclude 'trainingData' --exclude 'extraConfigs' --exclude 'customFiles' --exclude 'logs' \
    ./ "$PI_HOST:$REMOTE_DIR/"

# 3. Check for .env file
echo "🔑 Checking environment configuration..."
if ssh "$PI_HOST" "[ ! -f $REMOTE_DIR/stock-watcher/.env ]"; then
    if [ -f "./stock-watcher/.env" ]; then
        echo "Uploading local .env to Pi..."
        rsync -avz ./stock-watcher/.env "$PI_HOST:$REMOTE_DIR/stock-watcher/.env"
    fi
fi

# 4. Prepare data directories
echo "📁 Preparing service data directories..."
ssh "$PI_HOST" "mkdir -p $REMOTE_DIR/cowrie-honeypot/cowrie-data/{logs,downloads,keys} && sudo chmod -R 777 $REMOTE_DIR/cowrie-honeypot/cowrie-data"
ssh "$PI_HOST" "mkdir -p $REMOTE_DIR/uptime-kuma/data"
ssh "$PI_HOST" "mkdir -p $REMOTE_DIR/adguard-home/{work,conf}"
ssh "$PI_HOST" "mkdir -p $REMOTE_DIR/disposable-browser/config"
ssh "$PI_HOST" "mkdir -p $REMOTE_DIR/vaultwarden/{data,caddy_data}"
ssh "$PI_HOST" "mkdir -p $REMOTE_DIR/stirling-pdf/{trainingData,extraConfigs,customFiles,logs}"
ssh "$PI_HOST" "mkdir -p $REMOTE_DIR/syncthing/{config,Sync}"
ssh "$PI_HOST" "mkdir -p $REMOTE_DIR/docker-registry/data"

# 5. Launch containers
echo "🐳 Launching Docker containers on the Pi..."
ssh "$PI_HOST" "cd $REMOTE_DIR/stock-watcher && docker compose up -d --build"
ssh "$PI_HOST" "cd $REMOTE_DIR/cowrie-honeypot && docker compose up -d --build"
ssh "$PI_HOST" "cd $REMOTE_DIR/uptime-kuma && docker compose up -d"
ssh "$PI_HOST" "cd $REMOTE_DIR/adguard-home && docker compose up -d"
ssh "$PI_HOST" "cd $REMOTE_DIR/disposable-browser && docker compose up -d"
ssh "$PI_HOST" "cd $REMOTE_DIR/vaultwarden && docker compose up -d"
ssh "$PI_HOST" "cd $REMOTE_DIR/stirling-pdf && docker compose up -d"
ssh "$PI_HOST" "cd $REMOTE_DIR/syncthing && docker compose up -d"
ssh "$PI_HOST" "cd $REMOTE_DIR/docker-registry && docker compose up -d"

echo "=========================================================="
echo "🎉 Deployment Complete! Live Containers on your Pi:"
echo "=========================================================="
ssh "$PI_HOST" "docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'"
