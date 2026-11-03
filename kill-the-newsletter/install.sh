#!/usr/bin/env bash
# Installs Kill the Newsletter on an Ubuntu server and starts it.
# Run on the server as root:
#   bash install.sh <hostname> <your-email> [version]
# e.g.
#   bash install.sh news.example.com you@example.com v2.1.3
set -euo pipefail

HOST="${1:?Usage: bash install.sh <hostname> <your-email> [version]}"
ADMIN_EMAIL="${2:?Usage: bash install.sh <hostname> <your-email> [version]}"
VERSION="${3:-v2.1.3}"
DIR="${KTN_DIR:-/root/kill-the-newsletter}"
SERVICE_FILE="${KTN_SERVICE_FILE:-/etc/systemd/system/kill-the-newsletter.service}"

[[ "$(id -u)" -eq 0 ]] || { echo "Run this as root."; exit 1; }
[[ "$HOST" =~ ^[A-Za-z0-9.-]+$ ]] || { echo "Hostname looks wrong: $HOST"; exit 1; }
[[ "$ADMIN_EMAIL" =~ ^[^\"[:space:]]+@[^\"[:space:]]+$ ]] || { echo "Email looks wrong: $ADMIN_EMAIL"; exit 1; }
[[ -e "$DIR/kill-the-newsletter" ]] && { echo "Already installed in $DIR. Use upgrade.sh instead."; exit 1; }

# Something else already receiving mail would block port 25
if (exec 3<>/dev/tcp/127.0.0.1/25) 2>/dev/null; then
  echo "Port 25 is already in use (another mail server?). Find it with: ss -ltnp | grep ':25'"
  exit 1
fi

# DNS check (warning only)
RESOLVED="$(getent ahostsv4 "$HOST" | awk 'NR==1 {print $1}')"
if [[ -z "$RESOLVED" ]]; then
  echo "Warning: $HOST doesn't resolve yet. HTTPS won't work until its A record points here."
elif ! hostname -I 2>/dev/null | tr ' ' '\n' | grep -qx "$RESOLVED"; then
  echo "Warning: $HOST points to $RESOLVED, which isn't one of this server's addresses ($(hostname -I 2>/dev/null))."
fi

echo "Downloading Kill the Newsletter $VERSION..."
mkdir -p "$DIR"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
curl -fsSL -o "$TMP/ktn.tar.gz" \
  "https://github.com/leafac/kill-the-newsletter/releases/download/$VERSION/kill-the-newsletter--ubuntu--$VERSION.tar.gz"
tar -xzf "$TMP/ktn.tar.gz" -C "$DIR"

# The mail server uses the HTTPS certificate that the built-in Caddy gets from Let's Encrypt
CERTS="/root/.local/share/caddy/certificates/acme-v02.api.letsencrypt.org-directory/$HOST"
cat > "$DIR/configuration.mjs" <<CONFIG
export default {
  hostname: "$HOST",
  systemAdministratorEmail: "$ADMIN_EMAIL",
  tls: {
    key: "$CERTS/$HOST.key",
    certificate: "$CERTS/$HOST.crt",
  },
  dataDirectory: "$DIR/data/",
};
CONFIG

cat > "$SERVICE_FILE" <<SERVICE
[Unit]
Description=Kill the Newsletter!
After=network.target

[Service]
WorkingDirectory=$DIR/
ExecStart=$DIR/kill-the-newsletter/kill-the-newsletter $DIR/configuration.mjs
User=root
Restart=always

[Install]
WantedBy=multi-user.target
SERVICE

# Open the firewall if ufw is on: SSH, mail, HTTP (certificates), HTTPS
if command -v ufw >/dev/null && ufw status | grep -q "Status: active"; then
  ufw allow 22/tcp >/dev/null; ufw allow 25/tcp >/dev/null; ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null
  echo "Opened ports 22, 25, 80, 443 in ufw."
fi

systemctl daemon-reload
systemctl enable --now kill-the-newsletter

cat <<DONE

Kill the Newsletter is starting.
- Web:   https://$HOST  (the certificate can take a minute on first start)
- Data:  $DIR/data/
- Logs:  journalctl -u kill-the-newsletter -f

The mail server restarts on its own until the certificate exists, so a few
early errors about a missing .key or .crt file are expected.
DONE
