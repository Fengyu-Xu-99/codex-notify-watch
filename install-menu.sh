#!/usr/bin/env bash
# Install the optional Codex Watch macOS menu bar app.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SWIFT_SRC="$SCRIPT_DIR/CodexWatchMenu.swift"
MENU_BIN="$HOME/.codex/codex-watch-menu"
PLIST_LABEL="com.codex-watch-menu"
PLIST_PATH="$HOME/Library/LaunchAgents/$PLIST_LABEL.plist"
LOG_OUT="$HOME/.codex/codex-watch-menu.stdout.log"
LOG_ERR="$HOME/.codex/codex-watch-menu.stderr.log"

if ! command -v swiftc &>/dev/null; then
  echo "error: swiftc not found. Install Xcode Command Line Tools first." >&2
  exit 1
fi

if ! command -v python3 &>/dev/null; then
  echo "error: python3 not found" >&2
  exit 1
fi

mkdir -p "$HOME/.codex"
mkdir -p "$HOME/Library/LaunchAgents"

swiftc "$SWIFT_SRC" -o "$MENU_BIN"

launchctl unload "$PLIST_PATH" 2>/dev/null || true
pkill -f "$MENU_BIN" 2>/dev/null || true

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
    <string>$MENU_BIN</string>
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
</dict>
</plist>
PLIST

launchctl load -w "$PLIST_PATH"

echo "Installed menu app: $MENU_BIN"
echo "Agent loaded: $PLIST_PATH"
echo "Look for 'Codex' in the macOS menu bar."
echo ""
echo "Uninstall menu: \"$SCRIPT_DIR/uninstall-menu.sh\""
