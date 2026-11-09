#!/usr/bin/env bash
# Prepares the Kill the Newsletter server to also run Miniflux and Maniflux.
# Run on the server as root, after resizing it to 4 GB (see README.md):
#   scp server/setup.sh root@SERVER_IP:
#   ssh -t root@SERVER_IP 'bash setup.sh "Personal"'
# The argument is your vault's name in Obsidian Sync (`ob sync-list-remote` lists them).
# Keep the outer single quotes: ssh drops one level of quoting.
#
# Installs Docker, Tailscale (as the device "home") and Obsidian Headless, and keeps a
# copy of the vault (notes and attachments, no Obsidian settings) in /srv/vault with Obsidian Sync.
# Safe to run again.
set -euo pipefail

VAULT_NAME="${1:?Usage: bash setup.sh \"<vault name in Obsidian Sync>\"}"
TS_NAME="${TS_NAME:-home}"
VAULT_DIR=/srv/vault
LINKED=/etc/feed-vault-linked

[[ $EUID -eq 0 ]] || { echo "Run as root." >&2; exit 1; }
export DEBIAN_FRONTEND=noninteractive

mem_mb=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
if (( mem_mb < 3000 )); then
  echo "This server has ${mem_mb} MB of memory. Miniflux, Maniflux and Kill the Newsletter together"
  echo "need about 1.5 GB plus room to build; resize it to 4 GB first (see README.md)."
  read -r -p "Continue anyway? [y/N] " a; [[ "$a" =~ ^[Yy] ]] || exit 1
fi

echo "== Docker"
if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sh
fi
systemctl enable --now docker >/dev/null

echo "== Tailscale"
if ! command -v tailscale >/dev/null; then
  curl -fsSL https://tailscale.com/install.sh | sh
fi
if ! tailscale status >/dev/null 2>&1; then
  echo "Open the link below to add this server to your Tailscale account."
  tailscale up --hostname="$TS_NAME"
fi
TAILNET_HOST="$(tailscale status --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')"
until tailscale cert --cert-file /tmp/ts-test.crt --key-file /tmp/ts-test.key "$TAILNET_HOST" >/dev/null 2>&1; do
  echo
  echo "HTTPS isn't enabled for your tailnet yet. In the Tailscale admin console, go to DNS,"
  echo "make sure MagicDNS is on, and click 'Enable HTTPS'."
  read -r -p "Press Enter once it's enabled... "
done
rm -f /tmp/ts-test.crt /tmp/ts-test.key
# If ufw is on, let everything in over Tailscale (the Hetzner firewall still guards the public side)
if command -v ufw >/dev/null && ufw status | grep -q "Status: active"; then
  ufw allow in on tailscale0 >/dev/null
fi

echo "== Obsidian Headless (Obsidian Sync without the app)"
if ! command -v node >/dev/null || (( $(node -p 'process.versions.node.split(".")[0]') < 22 )); then
  curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
  apt-get install -y -q nodejs
fi
command -v ob >/dev/null || npm install -g obsidian-headless
OB="$(command -v ob)"

if [[ ! -f "$LINKED" ]]; then
  if ! "$OB" sync-list-remote >/dev/null 2>&1; then
    echo "Log in with your Obsidian account."
    "$OB" login
  fi
  mkdir -p "$VAULT_DIR"
  "$OB" sync-setup --vault "$VAULT_NAME" --path "$VAULT_DIR" --device-name "home server"
  # Notes and attachments, but no Obsidian settings or plugins. On a conflict, keep both versions.
  "$OB" sync-config --path "$VAULT_DIR" --configs "" --conflict-strategy conflict
  echo "First sync (can take a few minutes)..."
  "$OB" sync --path "$VAULT_DIR"
  touch "$LINKED"
fi

cat > /etc/systemd/system/obsidian-sync.service <<EOF
[Unit]
Description=Obsidian Sync for $VAULT_DIR
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=$OB sync --continuous --path $VAULT_DIR
Restart=always
RestartSec=30
User=root

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now obsidian-sync >/dev/null
systemctl restart obsidian-sync

mkdir -p /root/feed
notes=$(find "$VAULT_DIR/content" -maxdepth 1 -name '*.md' 2>/dev/null | wc -l)

echo
echo "Done."
echo "  Tailscale name: $TAILNET_HOST  (ssh root@$TS_NAME should now work from your Mac)"
echo "  Vault copy:     $VAULT_DIR, with $notes notes in content/"
echo "  Sync log:       journalctl -u obsidian-sync -f"
echo "Next: on your Mac, run server/move-from-mac.sh root@$TS_NAME"
