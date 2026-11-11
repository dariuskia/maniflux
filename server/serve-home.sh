#!/usr/bin/env bash
# Makes Maniflux and Miniflux reachable over Tailscale, through Kill the Newsletter's own web server:
#   https://home.<tailnet>.ts.net        -> Maniflux
#   https://home.<tailnet>.ts.net:8443   -> Miniflux
#   https://home.<tailnet>.ts.net:8444   -> the library service (saves from Link Ingest)
# Anyone not connecting through Tailscale is refused. Run on the server as root:
#   bash serve-home.sh
# It adds an extraCaddyfile to Kill the Newsletter's configuration.mjs, restarts it, and puts
# the old configuration back if Kill the Newsletter's own page stops loading. Safe to run again.
set -euo pipefail

KTN_DIR="${KTN_DIR:-/root/kill-the-newsletter}"
CONFIG="$KTN_DIR/configuration.mjs"
RANGES="100.64.0.0/10 fd7a:115c:a1e0::/48"

[[ $EUID -eq 0 ]] || { echo "Run as root." >&2; exit 1; }
[[ -f "$CONFIG" ]] || { echo "Not found: $CONFIG" >&2; exit 1; }
TS_JSON="$(tailscale status --json)"
TAILNET_HOST="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["Self"]["DNSName"].rstrip("."))' "$TS_JSON")"
TS_IP="$(tailscale ip -4 | head -1)"
KTN_HOST="$(sed -n 's/^ *hostname: *"\([^"]*\)".*/\1/p' "$CONFIG" | head -1)"

SITES="$(cat <<EOF
$TAILNET_HOST {
	@outside not remote_ip $RANGES
	abort @outside
	reverse_proxy 127.0.0.1:8090
}

$TAILNET_HOST:8443 {
	@outside not remote_ip $RANGES
	abort @outside
	reverse_proxy 127.0.0.1:8080
}

$TAILNET_HOST:8444 {
	@outside not remote_ip $RANGES
	abort @outside
	reverse_proxy 127.0.0.1:8095
}
EOF
)"

# Check the new sites on their own with the Caddy that Kill the Newsletter ships, if it can be found
CADDY="$(find "$KTN_DIR/kill-the-newsletter" -type f -name caddy -perm -u+x 2>/dev/null | head -1 || true)"
if [[ -n "$CADDY" ]]; then
  printf '%s\n' "$SITES" > /tmp/home-sites.caddyfile
  "$CADDY" adapt --adapter caddyfile --config /tmp/home-sites.caddyfile >/dev/null
  rm -f /tmp/home-sites.caddyfile
fi

cp "$CONFIG" "$CONFIG.before-home"
python3 - "$CONFIG" "$SITES" <<'EOF'
import re, sys
path, sites = sys.argv[1:]
text = open(path).read()
text = re.sub(r"\n  // home sites: start.*?// home sites: end\n", "\n", text, flags=re.S)
block = (
    "  // home sites: start (written by serve-home.sh; edit there, not here)\n"
    "  extraCaddyfile: `\n" + sites + "\n`,\n"
    "  // home sites: end\n"
)
if "extraCaddyfile" in re.sub(r"//.*", "", text):
    sys.exit("configuration.mjs already has its own extraCaddyfile; merge it by hand.")
end = text.rstrip().rfind("};")
text = text[:end] + block + text[end:]
open(path, "w").write(text)
EOF

systemctl restart kill-the-newsletter
echo "Restarted Kill the Newsletter; checking both addresses (up to a minute)..."
ok_news=0; ok_home=0
for _ in $(seq 30); do
  sleep 2
  curl -fsS -o /dev/null --resolve "$KTN_HOST:443:127.0.0.1" "https://$KTN_HOST/" && ok_news=1
  curl -sS -o /dev/null --resolve "$TAILNET_HOST:443:$TS_IP" "https://$TAILNET_HOST/" 2>/dev/null && ok_home=1
  (( ok_news && ok_home )) && break
done

if (( ! ok_news )); then
  echo "Kill the Newsletter's page isn't loading. Putting the old configuration back."
  cp "$CONFIG.before-home" "$CONFIG"
  systemctl restart kill-the-newsletter
  exit 1
fi
echo "  https://$KTN_HOST: OK"
if (( ok_home )); then
  echo "  https://$TAILNET_HOST: OK"
else
  echo "  https://$TAILNET_HOST: not answering yet. If Maniflux isn't running yet, that's expected."
fi
echo "Maniflux: https://$TAILNET_HOST    Miniflux: https://$TAILNET_HOST:8443"
