#!/bin/bash
# Initial Let's Encrypt certificate setup for eddieisagoodboy.com
#
# Run this ONCE on the server before starting the full stack:
#   chmod +x init-letsencrypt.sh && ./init-letsencrypt.sh
#
# Prerequisites:
#   - Domain A record pointing to this server's IP
#   - Docker and docker compose installed

set -e

DOMAIN="eddieisagoodboy.com"
EMAIL="cjmuldoon@gmail.com"  # For Let's Encrypt notifications
STAGING=0  # Set to 1 to test against staging (avoid rate limits)

echo "Creating directories..."
mkdir -p certbot/conf certbot/www

# Start nginx with HTTP-only config for the ACME challenge
echo "Starting nginx for ACME challenge..."

# Temporarily replace nginx config with HTTP-only version
cat > nginx-init.conf << 'EOF'
server {
    listen 80;
    server_name eddieisagoodboy.com www.eddieisagoodboy.com;

    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }

    location / {
        return 200 'Setting up SSL...';
        add_header Content-Type text/plain;
    }
}
EOF

docker compose up -d nginx 2>/dev/null || docker-compose up -d nginx

# Request certificate
echo "Requesting Let's Encrypt certificate for $DOMAIN..."

STAGING_ARG=""
if [ "$STAGING" -eq 1 ]; then
    STAGING_ARG="--staging"
fi

docker compose run --rm certbot certonly \
    --webroot \
    --webroot-path=/var/www/certbot \
    --email "$EMAIL" \
    --agree-tos \
    --no-eff-email \
    $STAGING_ARG \
    -d "$DOMAIN" \
    -d "www.$DOMAIN"

# Clean up temp config
rm -f nginx-init.conf

echo ""
echo "Certificate obtained! Now restart the full stack:"
echo "  docker compose down && docker compose up -d --build"
echo ""
echo "Certificate auto-renewal is handled by the certbot service."
