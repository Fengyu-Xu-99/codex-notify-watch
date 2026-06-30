#!/usr/bin/env bash
# Remove the codex-notify-watch LaunchAgent.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALLED_PYSCRIPT="$HOME/.codex/codex_notify_watch.py"
REPO_PYSCRIPT="$SCRIPT_DIR/codex_notify_watch.py"
PLIST_LABEL="com.codex-notify-watch"
PLIST_PATH="$HOME/Library/LaunchAgents/$PLIST_LABEL.plist"

PYTHON3="$(command -v python3 2>/dev/null || true)"

# Stop running instance
if [[ -n "$PYTHON3" ]]; then
  if [[ -f "$INSTALLED_PYSCRIPT" ]]; then
    "$PYTHON3" "$INSTALLED_PYSCRIPT" --stop 2>/dev/null || true
  elif [[ -f "$REPO_PYSCRIPT" ]]; then
    "$PYTHON3" "$REPO_PYSCRIPT" --stop 2>/dev/null || true
  fi
fi

if [[ -f "$PLIST_PATH" ]]; then
  launchctl unload "$PLIST_PATH" 2>/dev/null || true
  rm -f "$PLIST_PATH"
  echo "Removed: $PLIST_PATH"
else
  echo "No plist found at $PLIST_PATH (already uninstalled?)"
fi

echo "Done. codex-notify-watch will no longer start at login."
