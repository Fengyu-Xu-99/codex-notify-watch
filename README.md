# codex-notify-watch

macOS desktop notifications and sounds for [OpenAI Codex in VS Code](https://marketplace.visualstudio.com/items?itemName=GitHub.copilot).
Get an on-screen banner with a distinct sound whenever Codex finishes a task, pauses for your approval, or asks you a question.

| Event | Banner | Sound |
|-------|--------|-------|
| Task finished | **Codex: `<session>`** — Finished | Ping |
| Needs your approval | **Codex: `<session>`** — Needs permission: `exec_command` | Glass |
| Asking you a question | **Codex: `<session>`** — Needs your answer: `...` | Tink |

The banner names the session so you always know which Codex window needs attention.

## Requirements

- macOS
- Python 3.10+ (standard library only, no pip installs)
- Codex VS Code extension (logs to `~/.codex/logs_2.sqlite`)

## Install

```sh
git clone https://github.com/your-username/codex-notify-watch.git
cd codex-notify-watch
./install.sh
```

The installer:
1. Copies `codex_notify_watch.py` to `~/.codex/` (required -- launchd cannot read `~/Desktop` due to macOS TCC restrictions).
2. Writes a LaunchAgent plist to `~/Library/LaunchAgents/com.codex-notify-watch.plist`.
3. Loads the agent immediately -- the watcher starts right away.
4. On every subsequent login the watcher starts automatically, no terminal needed.

At the end it prints status, log, stop, and uninstall commands, plus a reminder to enable macOS notifications.

### One manual step (macOS won't let a script do this)

In **System Settings → Notifications**, enable notifications for **codex-watch-menu** if you installed the menu app. Otherwise enable **osascript**:
1. **Allow notifications: ON**
2. **Alert style: Banners** or **Alerts**

If banners land silently in Notification Center instead of popping, reset the stuck daemon:

```sh
killall NotificationCenter usernoted
```

## Uninstall

```sh
./uninstall.sh
```

Stops the watcher, unloads the LaunchAgent, and removes the plist. The script copy at `~/.codex/codex_notify_watch.py` is left in place (it's harmless and holds your state file).

## Status and manual control

```sh
# Version
python3 ~/.codex/codex_notify_watch.py --version

# Is it running?
python3 ~/.codex/codex_notify_watch.py --status
# running pid=12345
# not running

# Show useful Codex/watch log paths
python3 ~/.codex/codex_notify_watch.py --logs

# Stop it manually
python3 ~/.codex/codex_notify_watch.py --stop

# Dry run (logs alerts, no sound or banner)
python3 ~/.codex/codex_notify_watch.py --dry-run

# Self-test (fires mock alerts and exits)
python3 ~/.codex/codex_notify_watch.py --self-test

# Reprocess all existing log rows from the beginning
python3 ~/.codex/codex_notify_watch.py --from-beginning

# Recent session status
python3 ~/.codex/codex_notify_watch.py --sessions
```

## Optional menu bar app

V2 includes a small native macOS menu bar app. It does not replace the watcher; it reads status from the installed Python script.

```sh
./install-menu.sh
```

Look for **Codex** in the macOS menu bar. The title shows a count for running sessions, or `!N` when recent sessions need approval.

The menu includes:
- clickable recent session statuses
- manual refresh
- watcher status
- useful log paths
- quit

Clicking a session opens VS Code on the workspace Codex used for that session and asks the Codex extension to navigate to that local thread route when available. Notification banners are also delivered by the menu app when it is installed, so clicking **Show** uses the same routing instead of opening `osascript`.

Uninstall just the menu app:

```sh
./uninstall-menu.sh
```

The menu status is intentionally coarse. Codex completion rows are not always tied directly to a session in SQLite, so old inactive sessions are shown as `completed`, fresh log activity is shown as `running`, and only recent pending tool calls are shown as `needs approval`.

## How it works

Codex writes every session event to a local SQLite database at `~/.codex/logs_2.sqlite`. The watcher polls that file every 0.5 seconds and looks for three signals:

- **`turn/completed`** rows → task finished.
- **Approval tool calls** (`exec_command`, `apply_patch`, `request_plugin_install`) with no matching dispatch within the grace window → Codex is waiting for your permission.
- **`request_user_input`** calls → Codex is asking you a question.

It reads session names from `~/.codex/session_index.jsonl` to label banners. It never writes to Codex files and does not modify the VS Code extension.

**Duplicate protection:** a PID file and lock file ensure only one watcher runs at a time. A second invocation prints `codex-notify-watch is already running` and exits immediately.

**Auto-restart:** the LaunchAgent uses `KeepAlive` with a 10-second throttle, so if the watcher crashes it restarts automatically.

## Files

```text
# Codex data (read-only)
~/.codex/logs_2.sqlite
~/.codex/session_index.jsonl

# Watcher runtime
~/.codex/codex_notify_watch.py          # script copy (written by install.sh)
~/.codex/codex-notify-watch.state.json  # last processed log row id
~/.codex/codex-notify-watch.log         # debug log
~/.codex/codex-notify-watch.pid         # PID of running watcher
~/.codex/codex-notify-watch.lock        # duplicate-start lock
~/.codex/codex-notify-watch.stdout.log  # launchd stdout capture
~/.codex/codex-notify-watch.stderr.log  # launchd stderr capture

# Optional menu bar runtime
~/.codex/codex-watch-menu               # menu app binary (written by install-menu.sh)
~/.codex/codex-watch-menu.stdout.log    # menu stdout capture
~/.codex/codex-watch-menu.stderr.log    # menu stderr capture
~/.codex/codex-watch-menu-notifications.jsonl # click-aware notification queue

# LaunchAgent
~/Library/LaunchAgents/com.codex-notify-watch.plist
~/Library/LaunchAgents/com.codex-watch-menu.plist
```

## Options

```text
--db PATH                 Codex SQLite log DB. Default: ~/.codex/logs_2.sqlite
--session-index PATH      Codex session name index. Default: ~/.codex/session_index.jsonl
--state PATH              Last processed log id file.
--log PATH                Watcher debug log.
--pid-file PATH           Watcher PID file.
--lock-file PATH          Watcher lock file.
--poll SECONDS            Poll interval. Default: 0.5
--approval-grace SECONDS  Delay before approval heuristic fires. Default: 1.5
--dry-run                 Log alerts without playing sound or showing banners.
--from-beginning          Process all existing log rows, not just new ones.
--version                 Print version and exit.
--self-test               Run built-in dry tests and exit.
--status                  Print running status and exit.
--logs                    Print useful Codex/watch log paths and exit.
--sessions                Print recent Codex session statuses and exit.
--sessions-json           Print recent Codex session statuses as JSON.
--sessions-limit N        Number of recent sessions to show. Default: 8.
--stop                    Stop the running watcher and exit.
```

## Updating

After pulling new changes, re-run `./install.sh`. It stops the running watcher, copies the updated script to `~/.codex/`, and restarts the LaunchAgent.

## Troubleshooting

**Watcher shows `not running` right after install**
Check `~/.codex/codex-notify-watch.stderr.log`. The most common cause is a macOS TCC permission block -- make sure the script is being run from `~/.codex/` (the installer handles this) and not directly from `~/Desktop`.

**Banners appear in Notification Center but don't pop**
The notification daemon is stuck. Run:
```sh
killall NotificationCenter usernoted
```

**No banners appear**
Open **System Settings → Notifications → osascript** and enable notifications. If `osascript` is missing from the list, run:
```sh
osascript -e 'display notification "Test" with title "Codex Watch"'
```
Then check System Settings again.

**No sound but banners appear**
Sounds are played via `afplay /System/Library/Sounds/<name>.aiff` independently of the banner, so this shouldn't happen. Check that the sound files exist:
```sh
ls /System/Library/Sounds/
```

**Watcher keeps restarting in a loop**
Check `~/.codex/codex-notify-watch.stderr.log` for a recurring error. The 10-second `ThrottleInterval` in the plist prevents a tight crash loop.

## License

MIT
