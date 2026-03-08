#!/bin/bash
# Deploy barkup to DigitalOcean droplet via GitHub
set -e

SERVER="root@170.64.154.41"
APP_DIR="/opt/barkup"
REPO="https://github.com/cjmuldoon/barkup.git"

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

# Install git if not present
if ! command -v git &> /dev/null; then
    apt-get update && apt-get install -y git
fi

mkdir -p /opt/barkup/clips
SETUP

echo "=== Cloning/pulling repo ==="
ssh $SERVER "bash -s" << PULL
if [ -d $APP_DIR/.git ]; then
    cd $APP_DIR && git pull
else
    rm -rf $APP_DIR
    git clone $REPO $APP_DIR
fi
PULL

echo "=== Copying secrets (not in repo) ==="
scp /Users/dunderdoon/Projects_Local/barkup/.env $SERVER:$APP_DIR/.env
scp /Users/dunderdoon/Projects_Local/barkup/automations-489603-9d6e8cc38214.json $SERVER:$APP_DIR/service-account.json

echo "=== Building and starting ==="
ssh $SERVER "cd $APP_DIR && docker compose up -d --build"

echo "=== Checking status ==="
ssh $SERVER "cd $APP_DIR && docker compose ps && echo '---' && docker compose logs --tail=20"

echo ""
echo "=== Deploy complete ==="
echo "Monitor logs: ssh $SERVER 'cd $APP_DIR && docker compose logs -f'"
