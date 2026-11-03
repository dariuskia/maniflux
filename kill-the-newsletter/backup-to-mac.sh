#!/usr/bin/env bash
# Copies the server's data to your Mac. Run on your Mac:
#   ./backup-to-mac.sh
# Uses the hostname saved in .hostname (created by new-inbox.sh)
set -euo pipefail
HOSTNAME_FILE="$(dirname "$0")/.hostname"
[[ -s "$HOSTNAME_FILE" ]] || { echo "No saved hostname yet. Run ./new-inbox.sh once first."; exit 1; }
HOST="$(head -n 1 "$HOSTNAME_FILE")"
DEST="${1:-$HOME/Backups/kill-the-newsletter}"
mkdir -p "$DEST"
rsync -az --delete "root@$HOST:/root/kill-the-newsletter/data/" "$DEST/"
echo "Backed up to $DEST"
