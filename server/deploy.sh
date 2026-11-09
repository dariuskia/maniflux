#!/usr/bin/env bash
# Copies this folder's code to the personal server and rebuilds what changed. Run on the Mac:
#   server/deploy.sh [root@home]
# Never copies .env, the setup notes, or any data; the server keeps its own .env.
set -euo pipefail
cd "$(dirname "$0")/.."
SERVER="${1:-root@home}"

rsync -az --delete \
  --exclude .git --exclude .env --exclude docs/SETUP-NOTES.md \
  --exclude .venv --exclude __pycache__ --exclude .pytest_cache --exclude .DS_Store \
  --exclude 'kill-the-newsletter/.hostname' --exclude 'session-*' --exclude 'ig-session*' \
  --exclude 'ig2rss/session*' --exclude data/ --exclude '*.db' --exclude 'backup-*.sql' \
  ./ "$SERVER:/root/feed/"

ssh "$SERVER" 'cd /root/feed && [ -f .env ] && docker compose up -d --build --remove-orphans || echo "No .env on the server yet; run server/move-from-mac.sh first."'
