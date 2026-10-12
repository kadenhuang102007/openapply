#!/usr/bin/env bash
# Install the 8:00 AM `openapply daily` job for the current user.
#
# Step by step:
#   1. Fill the project path into scripts/com.openapply.daily.plist.
#   2. Copy it to ~/Library/LaunchAgents/.
#   3. Load it with launchctl (unloading any older copy first).
# Uninstall:  launchctl bootout gui/$(id -u)/com.openapply.daily && rm ~/Library/LaunchAgents/com.openapply.daily.plist
# Test now:   launchctl kickstart gui/$(id -u)/com.openapply.daily   (sends a REAL iMessage)
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.openapply.daily"
DEST="$HOME/Library/LaunchAgents/$LABEL.plist"

if [[ ! -x "$PROJECT_DIR/.venv/bin/openapply" ]]; then
  echo "error: $PROJECT_DIR/.venv/bin/openapply not found. Run setup first (see README)." >&2
  exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents" "$PROJECT_DIR/logs"
sed "s#__PROJECT_DIR__#$PROJECT_DIR#g" "$PROJECT_DIR/scripts/$LABEL.plist" > "$DEST"
plutil -lint "$DEST" >/dev/null

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$DEST"

echo "Installed $DEST"
echo "openapply daily will run at 8:00 AM. Logs: $PROJECT_DIR/logs/daily.{out,err}.log"
