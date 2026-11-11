#!/usr/bin/env bash
# Copies this folder's code to the personal server and rebuilds what changed. Run on the Mac:
#   server/deploy.sh [--force] [root@home]
# Never copies .env, the setup notes, or any data; the server keeps its own .env.
# Stops if there are uncommitted changes, so the server only runs code that's in Git.
# --force deploys anyway (for trying something out on the server before committing).
set -euo pipefail
cd "$(dirname "$0")/.."
FORCE=0
if [[ "${1:-}" == "--force" ]]; then FORCE=1; shift; fi
SERVER="${1:-root@home}"

if (( ! FORCE )) && [[ -n "$(git status --porcelain)" ]]; then
  echo "Not deploying: these changes aren't committed:" >&2
  git status --short >&2
  echo "Commit them first, or run server/deploy.sh --force to deploy anyway." >&2
  exit 1
fi

rsync -az --delete \
  --exclude .git --exclude .env --exclude docs/SETUP-NOTES.md \
  --exclude .venv --exclude __pycache__ --exclude .pytest_cache --exclude .DS_Store \
  --exclude 'kill-the-newsletter/.hostname' --exclude 'session-*' --exclude 'ig-session*' \
  --exclude 'ig2rss/session*' --exclude data/ --exclude '*.db' --exclude 'backup-*.sql' \
  ./ "$SERVER:/root/feed/"

ssh "$SERVER" 'cd /root/feed && [ -f .env ] && docker compose up -d --build --remove-orphans || echo "No .env on the server yet; run server/move-from-mac.sh first."'
