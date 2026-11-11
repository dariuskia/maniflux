#!/usr/bin/env bash
# Copies Miniflux's database and Maniflux's data from the personal server to this Mac:
#   ~/feed-backups/miniflux-YYYY-MM-DD.sql.gz   (pg_dump of the Miniflux database)
#   ~/feed-backups/maniflux-YYYY-MM-DD.db.gz    (Maniflux's SQLite file)
# Keeps the newest 14 of each. Runs daily from launchd (see install-backup.sh); run by hand any time:
#   server/backup-to-mac.sh [root@home]
# Restoring is in server/README.md.
set -euo pipefail
SERVER="${1:-root@home}"
DEST="${FEED_BACKUP_DIR:-$HOME/feed-backups}"
KEEP=14
STAMP="$(date +%F)"
mkdir -p "$DEST"
cd "$DEST"

fail() { echo "$(date '+%F %T') backup failed: $*" >&2; rm -f .partial-*; exit 1; }
remote() { ssh -o BatchMode=yes -o ConnectTimeout=30 "$SERVER" "bash -o pipefail -c '$1'"; }

# Miniflux: a plain SQL dump, compressed on the server
remote 'cd /root/feed && docker compose exec -T db pg_dump -U miniflux miniflux | gzip' > .partial-miniflux.sql.gz \
  || fail "couldn't dump Miniflux (is Tailscale on?)"
gzip -dc .partial-miniflux.sql.gz | tail -n 5 | grep -q "PostgreSQL database dump complete" \
  || fail "the Miniflux dump is incomplete"
mv .partial-miniflux.sql.gz "miniflux-$STAMP.sql.gz"

# Maniflux: SQLite's own backup, so a save happening at the same moment can't leave a half-written copy
remote 'cd /root/feed && docker compose exec -T maniflux python -c "
import sqlite3, sys
src = sqlite3.connect(\"/data/maniflux.db\"); dst = sqlite3.connect(\"/tmp/backup.db\")
src.backup(dst); dst.close()
sys.stdout.buffer.write(open(\"/tmp/backup.db\", \"rb\").read())
" | gzip' > .partial-maniflux.db.gz || fail "couldn't copy Maniflux's data"
gzip -t .partial-maniflux.db.gz || fail "the Maniflux copy is damaged"
mv .partial-maniflux.db.gz "maniflux-$STAMP.db.gz"

# Keep the newest $KEEP of each
for kind in miniflux maniflux; do
  ls -1t "$kind"-*.gz 2>/dev/null | tail -n +"$((KEEP + 1))" | while read -r old; do rm -f "$old"; done
done

echo "$(date '+%F %T') backed up to $DEST: miniflux-$STAMP.sql.gz ($(du -h "miniflux-$STAMP.sql.gz" | cut -f1)), maniflux-$STAMP.db.gz ($(du -h "maniflux-$STAMP.db.gz" | cut -f1))"
