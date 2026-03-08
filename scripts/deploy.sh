#!/bin/bash
# Deploy barkup to a DigitalOcean droplet
set -e

SERVER="root@170.64.154.41"
APP_DIR="/opt/barkup"

echo "=== Setting up server ==="
ssh $SERVER 'bash -s' << 'SETUP'
# Install Docker if not present
if ! command -v docker &> /dev/null; then
    echo "Installing Docker..."
    curl -fsSL https://get.docker.com | sh
    systemctl enable docker
    systemctl start docker
fi

# Install Docker Compose plugin if not present
if ! docker compose version &> /dev/null; then
    echo "Installing Docker Compose..."
    apt-get update && apt-get install -y docker-compose-plugin
fi

# Create app directory
mkdir -p /opt/barkup/clips
SETUP

echo "=== Copying project files ==="
rsync -avz --exclude '.env' \
    --exclude 'clips/' \
    --exclude '__pycache__' \
    --exclude '.venv' \
    --exclude '*.pyc' \
    --exclude '.git' \
    -e ssh \
    /Users/dunderdoon/Projects_Local/barkup/ \
    $SERVER:$APP_DIR/

echo "=== Copying .env ==="
scp /Users/dunderdoon/Projects_Local/barkup/.env $SERVER:$APP_DIR/.env

echo "=== Building and starting ==="
ssh $SERVER "cd $APP_DIR && docker compose up -d --build"

echo "=== Checking status ==="
ssh $SERVER "cd $APP_DIR && docker compose ps && echo '---' && docker compose logs --tail=20"

echo ""
echo "=== Deploy complete ==="
echo "Monitor logs: ssh $SERVER 'cd $APP_DIR && docker compose logs -f'"
