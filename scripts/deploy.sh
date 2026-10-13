#!/usr/bin/env bash
# Deploy the latest new-version on this server.
#   Code-only changes (app/, img/, pdf/ are mounted into the container): pull + restart, seconds.
#   requirements.txt or Dockerfile changed: rebuild the image (layer cache keeps it short).
# Never run `docker builder prune` before deploying — it throws the cache away
# and the next build reinstalls Python packages and Chromium from scratch.
set -euo pipefail
cd "$(dirname "$0")/.."

before=$(git rev-parse HEAD)
git pull --ff-only
changed=$(git diff --name-only "$before" HEAD)

if [ "${1:-}" = "--build" ] || grep -qxE 'requirements.txt|Dockerfile' <<<"$changed"; then
    echo "Dependencies changed: rebuilding the image"
    sudo docker compose up -d --build
else
    sudo docker compose up -d
    sudo docker compose restart
fi
sudo docker image prune -f >/dev/null   # old image layers only; build cache stays
sudo docker compose ps
