# Codex Notify Watch

Local macOS prototype that watches Codex's local SQLite logs and alerts when a
Codex session finishes, waits for permission, or asks for user input.

This is not a VS Code extension yet. It is a small Python process that runs on
your Mac beside VS Code and reads the local Codex log files.

## Current Behavior

- Finish alert: detects `turn/completed` rows and plays `Ping`.
- Permission alert: detects approval-like tool/function calls that do not
  dispatch quickly and plays `Glass`.
- User-input alert: detects `request_user_input` calls and plays `Tink`.
- Banner title: `Codex: <session name>`, using names from
  `~/.codex/session_index.jsonl`.
- Banner message: includes `Finished`, `Needs permission: ...`, or
  `Needs your answer: ...`.
- Duplicate protection: creates a PID file and lock file so a second watcher
  will refuse to start.

## Tech Stack

- Python 3.10+ standard library only.
- SQLite read-only access to Codex logs.
- macOS sound through `/usr/bin/afplay`.
- macOS banner notifications through `/usr/bin/osascript`.

## Files Used

```text
~/.codex/logs_2.sqlite
~/.codex/session_index.jsonl
~/.codex/codex-notify-watch.state.json
~/.codex/codex-notify-watch.log
~/.codex/codex-notify-watch.pid
~/.codex/codex-notify-watch.lock
```

## Run

```sh
cd "/Users/fengyu/Desktop/Github Projects/codex-notify-watch"
PYTHONDONTWRITEBYTECODE=1 python3 codex_notify_watch.py
```

Leave that terminal/process running while using Codex in VS Code.

For a dry run with no sound or banner:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 codex_notify_watch.py --dry-run
```

## Status

```sh
cd "/Users/fengyu/Desktop/Github Projects/codex-notify-watch"
python3 codex_notify_watch.py --status
```

Expected output is one of:

```text
running pid=12345
not running
not running; stale pid=12345
```

## Stop

```sh
cd "/Users/fengyu/Desktop/Github Projects/codex-notify-watch"
python3 codex_notify_watch.py --stop
```

This sends a clean stop signal to the process recorded in
`~/.codex/codex-notify-watch.pid`.

## Test

```sh
cd "/Users/fengyu/Desktop/Github Projects/codex-notify-watch"
PYTHONDONTWRITEBYTECODE=1 python3 codex_notify_watch.py --self-test
```

Then run the watcher and ask Codex to do a harmless approval-required action.
Expected alerts:

- `Codex: <session name>` / `Needs permission: ...` with the `Glass` sound.
- `Codex: <session name>` / `Needs your answer: ...` with the `Tink` sound.
- `Codex: <session name>` / `Finished` with the `Ping` sound.

## Options

```text
--db PATH                 Codex SQLite log DB.
--session-index PATH      Codex session name index.
--state PATH              Last processed log id JSON file.
--log PATH                Watcher debug log.
--pid-file PATH           Watcher PID file.
--lock-file PATH          Watcher lock file.
--poll SECONDS            Poll interval. Default: 0.5.
--approval-grace SECONDS  Delay before approval heuristic fires. Default: 1.5.
--dry-run                 Log alerts without playing sound/showing banners.
--from-beginning          Process old log rows instead of starting from now.
--self-test               Run built-in dry tests and exit.
--status                  Print whether the watcher is running and exit.
--stop                    Stop the running watcher and exit.
```

## How It Runs

Right now it runs as a normal local Python process. If Codex starts it from this
chat/tool session, it keeps running as long as that process is alive. It is not
installed as a permanent macOS background service yet.

Closing VS Code does not automatically start the watcher next time. If your Mac
or the watcher process stops, start it again with the `Run` command above.

The watcher is independent of any one VS Code window. It watches the shared
local Codex log database, so it can alert for multiple Codex sessions and uses
the session name in the banner to show which one needs attention.

## Preventing Duplicate Watchers

The watcher uses `~/.codex/codex-notify-watch.lock` and
`~/.codex/codex-notify-watch.pid`. If one copy is already running, a second copy
prints `codex-notify-watch is already running` and exits.

Use `--status` to check the current process and `--stop` to shut it down.

## Future Packaging

Good next steps after local proof:

- Add a macOS LaunchAgent so it starts automatically at login.
- Add an install script.
- Add a tiny menu/status command.
- Later, consider a VS Code extension if we need deeper UI integration.

## Notes

- This is macOS-only for now.
- It does not modify the OpenAI VS Code extension.
- Approval detection is heuristic: if Codex logs an approval-like function/tool
  call, such as `exec_command`, `apply_patch`, or an MCP tool call, and no
  matching dispatch appears within the grace window, the watcher assumes Codex
  is waiting for permission.
