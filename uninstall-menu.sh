#!/usr/bin/env bash
# Remove the optional Codex Watch macOS menu bar app.

set -euo pipefail

MENU_BIN="$HOME/.codex/codex-watch-menu"
PLIST_LABEL="com.codex-watch-menu"
PLIST_PATH="$HOME/Library/LaunchAgents/$PLIST_LABEL.plist"

launchctl unload "$PLIST_PATH" 2>/dev/null || true
pkill -f "$MENU_BIN" 2>/dev/null || true

if [[ -f "$PLIST_PATH" ]]; then
  rm -f "$PLIST_PATH"
  echo "Removed: $PLIST_PATH"
else
  echo "No plist found at $PLIST_PATH (already uninstalled?)"
fi

if [[ -f "$MENU_BIN" ]]; then
  rm -f "$MENU_BIN"
  echo "Removed: $MENU_BIN"
fi

echo "Done. Codex Watch menu will no longer start at login."
