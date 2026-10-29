#!/usr/bin/env bash
# One-time setup on a fresh Ubuntu VM. Expects /opt/kinshield.env with the secrets (written by cloud-init).
set -euxo pipefail
export DEBIAN_FRONTEND=noninteractive
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sh
fi
systemctl enable --now docker
if [ ! -d /opt/kinshield/.git ]; then
  git clone --depth 1 https://github.com/gayatrisy/kinshield.git /opt/kinshield
fi
cd /opt/kinshield
IP=$(curl -fsS https://api.ipify.org)
DOMAIN="${IP//./-}.sslip.io"
cp /opt/kinshield.env .env
grep -q '^KINSHIELD_PUBLIC_URL=' .env && sed -i "s#^KINSHIELD_PUBLIC_URL=.*#KINSHIELD_PUBLIC_URL=https://${DOMAIN}#" .env \
  || echo "KINSHIELD_PUBLIC_URL=https://${DOMAIN}" >> .env
echo "DOMAIN=${DOMAIN}" > deploy/.env
docker compose -f deploy/compose.prod.yml --env-file deploy/.env up -d --build
echo "KinShield is starting at https://${DOMAIN}" | tee /opt/kinshield-url.txt
