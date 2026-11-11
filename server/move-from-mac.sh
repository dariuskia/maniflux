#!/usr/bin/env bash
# Moves Miniflux and Maniflux from this Mac to the personal server, and keeps ig2rss on the Mac
# with Miniflux reaching it over Tailscale. Run once, on the Mac, after server/setup.sh:
#   server/move-from-mac.sh [root@home]
#
# 1. Checks the server's vault copy has notes (Maniflux uses it to skip links already saved)
# 2. Copies the code, and makes the server's .env from this Mac's
# 3. Stops Miniflux and Maniflux here, then copies Miniflux's database and Maniflux's data over
# 4. Starts them on the server and serves them over Tailscale (server/serve-home.sh)
# 5. Shares ig2rss on the Mac's Tailscale address and points Miniflux's Instagram feeds at it
# 6. Leaves only ig2rss running here. The Mac's Miniflux and Maniflux data stay as a backup.
set -euo pipefail
cd "$(dirname "$0")/.."
SERVER="${1:-root@home}"
VAULT_CONTENT=/srv/vault/content

step() { printf '\n== %s\n' "$*"; }
remote() { ssh "$SERVER" "$@"; }

step "Checking the server"
remote true || { echo "Can't reach $SERVER over SSH. Is Tailscale on?" >&2; exit 1; }
remote 'command -v docker >/dev/null' || { echo "Docker isn't installed on the server; run server/setup.sh there first." >&2; exit 1; }
notes="$(remote "find $VAULT_CONTENT -maxdepth 1 -name '*.md' 2>/dev/null | wc -l" | tr -d ' ')"
here="$(find "$(grep '^VAULT_CONTENT_PATH=' .env | cut -d= -f2- | tr -d '"')" -maxdepth 1 -name '*.md' | wc -l | tr -d ' ')"
echo "Vault copy on the server: $notes notes in content/ (here: $here)"
if (( notes == 0 || notes < here * 9 / 10 )); then
  echo "The server's vault copy looks incomplete. Check: ssh $SERVER journalctl -u obsidian-sync -n 50" >&2
  exit 1
fi
if remote 'docker volume inspect feed_miniflux-db >/dev/null 2>&1'; then
  echo "The server already has a Miniflux database (feed_miniflux-db). Not overwriting it." >&2
  echo "To start over: ssh $SERVER 'cd /root/feed && docker compose down -v'" >&2
  exit 1
fi
TAILNET_HOST="$(remote "tailscale status --json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')"
MAC_TS_IP="$(tailscale ip -4 | head -1)"
echo "Server: $TAILNET_HOST    Mac: $MAC_TS_IP"

step "Copying the code and making the server's .env"
remote 'mkdir -p /root/feed'
server/deploy.sh "$SERVER" >/dev/null 2>&1 || true   # copies code; can't start anything without .env yet
# Same secrets as here; only what differs on the server is changed. Sent over SSH, never printed.
grep -v -E '^(COMPOSE_PROFILES|VAULT_CONTENT_PATH|MINIFLUX_BASE_URL|MANIFLUX_PUBLIC_URL)=' .env \
  | { cat; cat <<EOF
COMPOSE_PROFILES=reader
VAULT_CONTENT_PATH=$VAULT_CONTENT
MINIFLUX_BASE_URL=https://$TAILNET_HOST:8443/
MANIFLUX_PUBLIC_URL=https://$TAILNET_HOST
EOF
} | remote 'umask 077 && cat > /root/feed/.env'

step "Stopping Miniflux and Maniflux on the Mac"
docker compose stop maniflux miniflux

step "Copying Miniflux's database"
remote 'cd /root/feed && docker compose up -d --wait db'
docker compose exec -T db pg_dump -U miniflux -Fc miniflux \
  | remote 'cd /root/feed && docker compose exec -T db pg_restore -U miniflux -d miniflux --no-owner --exit-on-error'

step "Copying Maniflux's data"
docker run --rm -v feed_maniflux-data:/data:ro alpine tar -C /data -cf - . \
  | remote 'docker volume create feed_maniflux-data >/dev/null && docker run --rm -i -v feed_maniflux-data:/data alpine tar -C /data -xf -'

step "Starting everything on the server"
remote 'cd /root/feed && docker compose up -d --build --wait'
scp -q server/serve-home.sh "$SERVER:/root/serve-home.sh"
remote 'bash /root/serve-home.sh'

step "Sharing ig2rss over Tailscale and pointing Miniflux at it"
tailscale serve --bg --tcp=8091 tcp://127.0.0.1:8091   # plain forwarding: answers on the IP, not just the name
MINIFLUX_API_KEY="$(grep '^MINIFLUX_API_KEY=' .env | cut -d= -f2- | tr -d '"')" \
python3 - "https://$TAILNET_HOST:8443/v1" "http://ig2rss:8091/" "http://$MAC_TS_IP:8091/" <<'EOF'
import json, os, ssl, sys, urllib.request
api, old, new = sys.argv[1:]
try:  # python.org's Python on macOS has no certificates of its own until "Install Certificates" is run
    import certifi; ctx = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    ctx = ssl.create_default_context()
headers = {"X-Auth-Token": os.environ["MINIFLUX_API_KEY"], "Content-Type": "application/json"}
def call(method, path, body=None):
    req = urllib.request.Request(api + path, method=method, headers=headers,
                                 data=json.dumps(body).encode() if body is not None else None)
    with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
        return json.load(r) if r.status != 204 else None
import time
for attempt in range(12):  # the Tailscale certificate can take a moment on first use
    try:
        feeds = call("GET", "/feeds"); break
    except Exception as e:
        if attempt == 11: sys.exit(f"Couldn't reach Miniflux at {api}: {e}")
        time.sleep(5)
moved = 0
for feed in feeds:
    if feed["feed_url"].startswith(old):
        call("PUT", f"/feeds/{feed['id']}", {"feed_url": new + feed["feed_url"][len(old):]})
        moved += 1
print(f"Pointed {moved} Instagram feeds at {new}")
EOF

step "Leaving only ig2rss running on the Mac"
docker compose rm -sf maniflux miniflux db
sed -i '' 's/^COMPOSE_PROFILES=.*/COMPOSE_PROFILES=ig2rss/' .env

cat <<EOF

Done.
  Maniflux: https://$TAILNET_HOST
  Miniflux: https://$TAILNET_HOST:8443
  ig2rss:   http://127.0.0.1:8091 (on this Mac, shared with Miniflux over Tailscale)
The Mac's old Miniflux and Maniflux data are still in the feed_miniflux-db and feed_maniflux-data
volumes. Once you're happy with the server, remove them with:
  docker volume rm feed_miniflux-db feed_maniflux-data
EOF
