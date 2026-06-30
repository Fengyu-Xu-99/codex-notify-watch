#!/usr/bin/env bash
# Install codex-notify-watch as a macOS LaunchAgent so it starts at login.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYSCRIPT_SRC="$SCRIPT_DIR/codex_notify_watch.py"
PYSCRIPT="$HOME/.codex/codex_notify_watch.py"
PLIST_LABEL="com.codex-notify-watch"
PLIST_PATH="$HOME/Library/LaunchAgents/$PLIST_LABEL.plist"
LOG_OUT="$HOME/.codex/codex-notify-watch.stdout.log"
LOG_ERR="$HOME/.codex/codex-notify-watch.stderr.log"

# Require python3
if ! command -v python3 &>/dev/null; then
  echo "error: python3 not found" >&2
  exit 1
fi

PYTHON3="$(command -v python3)"

mkdir -p "$HOME/Library/LaunchAgents"
mkdir -p "$HOME/.codex"

# Copy script to ~/.codex so launchd can access it (Desktop is TCC-protected)
cp "$PYSCRIPT_SRC" "$PYSCRIPT"

# Stop any running instance first (ignore errors if not running)
"$PYTHON3" "$PYSCRIPT" --stop 2>/dev/null || true

cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$PLIST_LABEL</string>

  <key>ProgramArguments</key>
  <array>
    <string>$PYTHON3</string>
    <string>$PYSCRIPT</string>
  </array>

  <key>RunAtLoad</key>
  <true/>

  <key>KeepAlive</key>
  <dict>
    <key>SuccessfulExit</key>
    <false/>
  </dict>

  <key>StandardOutPath</key>
  <string>$LOG_OUT</string>

  <key>StandardErrorPath</key>
  <string>$LOG_ERR</string>

  <key>ThrottleInterval</key>
  <integer>10</integer>

  <key>EnvironmentVariables</key>
  <dict>
    <key>PYTHONDONTWRITEBYTECODE</key>
    <string>1</string>
    <key>HOME</key>
    <string>$HOME</string>
    <key>PATH</key>
    <string>/usr/local/bin:/usr/bin:/bin</string>
  </dict>
</dict>
</plist>
PLIST

# Load (or reload) the agent
launchctl unload "$PLIST_PATH" 2>/dev/null || true
launchctl load -w "$PLIST_PATH"

echo "Installed: $PLIST_PATH"
echo "Agent loaded and running. Starts automatically at login."
echo ""
echo "Check status:   python3 \"$PYSCRIPT\" --status"
echo "Show log paths: python3 \"$PYSCRIPT\" --logs"
echo "Stop manually:  python3 \"$PYSCRIPT\" --stop"
echo "Uninstall:      \"$SCRIPT_DIR/uninstall.sh\""
echo ""
echo "Reminder: enable notifications for codex-watch-menu if installed, or osascript otherwise."
