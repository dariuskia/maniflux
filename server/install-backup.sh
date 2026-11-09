#!/usr/bin/env bash
# Runs server/backup-to-mac.sh every day at noon from launchd (or when the Mac next wakes, if it
# was asleep then). The log is ~/feed-backups/backup.log. Run once on the Mac:
#   server/install-backup.sh
# To stop it: launchctl bootout gui/$(id -u)/com.darius.feed-backup && rm ~/Library/LaunchAgents/com.darius.feed-backup.plist
set -euo pipefail
SCRIPT="$(cd "$(dirname "$0")" && pwd)/backup-to-mac.sh"
LABEL=com.darius.feed-backup
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DEST="$HOME/feed-backups"
mkdir -p "$DEST" "$(dirname "$PLIST")"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>/bin/bash</string><string>$SCRIPT</string></array>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>12</integer><key>Minute</key><integer>0</integer></dict>
  <key>StandardOutPath</key><string>$DEST/backup.log</string>
  <key>StandardErrorPath</key><string>$DEST/backup.log</string>
</dict>
</plist>
EOF

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Daily backup at noon installed. Log: $DEST/backup.log"
echo "Run it now with: launchctl kickstart gui/$(id -u)/$LABEL"
