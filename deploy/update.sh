#!/usr/bin/env bash
# Pull the latest code and restart. Run on the VM: sudo bash /opt/kinshield/deploy/update.sh
set -euxo pipefail
cd /opt/kinshield
git pull --ff-only
docker compose -f deploy/compose.prod.yml --env-file deploy/.env up -d --build
