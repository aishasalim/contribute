#!/usr/bin/env bash
# Install (or replace) a launchd agent that harvests the radar once a day.
#
#   scripts/install_radar_launchd.sh            # 09:00 local, priority scope
#   scripts/install_radar_launchd.sh 7 30       # 07:30 local
#   launchctl bootout gui/$(id -u)/com.contributie.radar   # remove it
#
# launchd rather than cron: it survives sleep (a missed run fires on wake),
# needs no Full Disk Access prompt, and logs to artifacts/ under the repo.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOUR="${1:-9}"
MINUTE="${2:-0}"
LABEL="com.contributie.radar"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
UV="$(command -v uv || true)"

if [[ -z "$UV" ]]; then
  echo "uv not found on PATH; install it first (https://docs.astral.sh/uv/)." >&2
  exit 1
fi

mkdir -p "$ROOT/artifacts" "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$UV</string>
    <string>run</string>
    <string>--python</string><string>3.12</string>
    <string>python</string>
    <string>$ROOT/scripts/harvest.py</string>
  </array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>EnvironmentVariables</key>
  <dict><key>PATH</key><string>$(dirname "$UV"):/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin</string></dict>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>$HOUR</integer><key>Minute</key><integer>$MINUTE</integer></dict>
  <key>StandardOutPath</key><string>$ROOT/artifacts/radar.log</string>
  <key>StandardErrorPath</key><string>$ROOT/artifacts/radar.log</string>
</dict>
</plist>
EOF

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Installed $LABEL: daily at $(printf '%02d:%02d' "$HOUR" "$MINUTE"), log in artifacts/radar.log"
echo "Run it now:  launchctl kickstart -k gui/$(id -u)/$LABEL"
