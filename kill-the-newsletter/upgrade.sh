#!/usr/bin/env bash
# Upgrades Kill the Newsletter to a new version, backing up the data first.
# Run on the server as root:
#   bash upgrade.sh v2.1.4
# If the new version doesn't start, the previous one is put back.
set -euo pipefail

VERSION="${1:?Usage: bash upgrade.sh <version, e.g. v2.1.4>}"
DIR="${KTN_DIR:-/root/kill-the-newsletter}"
BACKUPS="${KTN_BACKUPS:-/root/kill-the-newsletter-backups}"
DEPLOY="$DIR--deploy"

[[ "$(id -u)" -eq 0 ]] || { echo "Run this as root."; exit 1; }
[[ -e "$DIR/kill-the-newsletter" ]] || { echo "Not installed in $DIR. Use install.sh first."; exit 1; }

rm -rf "$DEPLOY"; mkdir -p "$DEPLOY" "$BACKUPS"
echo "Downloading $VERSION..."
curl -fsSL -o "$DEPLOY/ktn.tar.gz" \
  "https://github.com/leafac/kill-the-newsletter/releases/download/$VERSION/kill-the-newsletter--ubuntu--$VERSION.tar.gz"
tar -xzf "$DEPLOY/ktn.tar.gz" -C "$DEPLOY"

systemctl stop kill-the-newsletter

BACKUP="$BACKUPS/data-$(date +%Y-%m-%d-%H%M%S).tar.gz"
tar -czf "$BACKUP" -C "$DIR" data
echo "Backed up data to $BACKUP"

mv "$DIR/kill-the-newsletter" "$DEPLOY/old"
mv "$DEPLOY/kill-the-newsletter" "$DIR/kill-the-newsletter"
systemctl start kill-the-newsletter

sleep "${KTN_WAIT:-15}"
if systemctl is-active --quiet kill-the-newsletter; then
  rm -rf "$DEPLOY"
  echo "Upgraded to $VERSION."
else
  echo "The new version didn't start. Putting the previous version back."
  systemctl stop kill-the-newsletter || true
  rm -rf "$DIR/kill-the-newsletter"
  mv "$DEPLOY/old" "$DIR/kill-the-newsletter"
  systemctl start kill-the-newsletter
  echo "Restored. Check: journalctl -u kill-the-newsletter -n 100"
  exit 1
fi
