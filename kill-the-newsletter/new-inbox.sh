#!/usr/bin/env bash
# Creates a Kill the Newsletter inbox and prints its email address and feed URL.
# Run on your Mac:
#   ./new-inbox.sh "Money Stuff"
# The first time, it asks for your server's hostname and saves it in .hostname
set -euo pipefail

NAME="${1:?Usage: ./new-inbox.sh \"<newsletter name>\"}"

HOSTNAME_FILE="$(dirname "$0")/.hostname"
if [[ ! -s "$HOSTNAME_FILE" ]]; then
  read -rp "Your Kill the Newsletter hostname (e.g. news.yourdomain.com): " HOST
  HOST="${HOST#https://}"; HOST="${HOST%/}"
  printf '%s\n' "$HOST" > "$HOSTNAME_FILE"
  echo "Saved to $HOSTNAME_FILE"
fi
HOST="$(head -n 1 "$HOSTNAME_FILE")"

RESPONSE="$(curl -fsS --request POST \
  --header "CSRF-Protection: true" --header "Accept: application/json" \
  --data-urlencode "title=$NAME" "https://$HOST/feeds")"

EMAIL="$(printf '%s' "$RESPONSE" | python3 -c 'import sys, json; print(json.load(sys.stdin)["email"])')"
FEED="$(printf '%s' "$RESPONSE" | python3 -c 'import sys, json; print(json.load(sys.stdin)["feed"])')"

echo "Subscribe with: $EMAIL"
echo "Feed URL:       $FEED"
if command -v pbcopy >/dev/null; then
  printf '%s' "$EMAIL" | pbcopy
  echo "(email address copied to the clipboard)"
fi
