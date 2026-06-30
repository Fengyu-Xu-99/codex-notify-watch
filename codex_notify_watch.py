#!/usr/bin/env python3
"""Watch local Codex logs and notify when Codex finishes or waits for approval."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shlex
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


HOME = Path.home()
CODEX_HOME = Path(os.environ.get("CODEX_HOME", HOME / ".codex")).expanduser()
DEFAULT_DB = CODEX_HOME / "logs_2.sqlite"
DEFAULT_SESSION_INDEX = CODEX_HOME / "session_index.jsonl"
DEFAULT_STATE = CODEX_HOME / "codex-notify-watch.state.json"
DEFAULT_LOG = CODEX_HOME / "codex-notify-watch.log"
DEFAULT_STDOUT_LOG = CODEX_HOME / "codex-notify-watch.stdout.log"
DEFAULT_STDERR_LOG = CODEX_HOME / "codex-notify-watch.stderr.log"
DEFAULT_MENU_BIN = CODEX_HOME / "codex-watch-menu"
DEFAULT_MENU_NOTIFY = CODEX_HOME / "codex-watch-menu-notifications.jsonl"
DEFAULT_PID = CODEX_HOME / "codex-notify-watch.pid"
DEFAULT_LOCK = CODEX_HOME / "codex-notify-watch.lock"
DEFAULT_POLL_SECONDS = 0.5
DEFAULT_APPROVAL_GRACE_SECONDS = 1.5
SESSION_ACTIVE_SECONDS = 15 * 60
SESSION_PENDING_SECONDS = 10 * 60
VERSION = "2.0.0"
FINISH_SOUND = "Ping"
APPROVAL_SOUND = "Glass"
USER_INPUT_SOUND = "Tink"
APPROVAL_TOOL_NAMES = {
    "apply_patch",
    "exec_command",
    "request_plugin_install",
}

CALL_ID_RE = re.compile(r'call_id[\\":\s]+["]?([A-Za-z0-9_-]+)')
FUNCTION_CALL_RE = re.compile(r'FunctionCall \{[^}]*name: "([^"]+)"[^}]*call_id: "([^"]+)"')
JSON_FUNCTION_CALL_RE = re.compile(r'"type"\s*:\s*"function_call"')
NAME_RE = re.compile(r'"name"\s*:\s*"([^"]+)"')
ARGS_RE = re.compile(r'"arguments"\s*:\s*"((?:\\.|[^"\\])*)"')
CMD_RE = re.compile(r'\\"cmd\\"\s*:\s*\\"((?:\\\\.|[^"\\])*)\\"')
QUESTION_RE = re.compile(r'\\"question\\"\s*:\s*\\"((?:\\\\.|[^"\\])*)\\"')
CWD_RE = re.compile(r" cwd=(.*?)(?= [A-Za-z0-9_.:-]+=|}|$)")


@dataclass
class LogRow:
    id: int
    ts: int
    ts_nanos: int
    level: str
    target: str
    body: str
    thread_id: str | None


@dataclass
class PendingCall:
    call_id: str
    tool_name: str
    first_seen: float
    thread_id: str | None
    summary: str
    alerted: bool = False
    dispatched: bool = False


@dataclass
class SessionInfo:
    thread_id: str
    title: str
    updated_at: str


@dataclass
class SessionStatus:
    thread_id: str
    title: str
    status: str
    updated_at: str
    last_log_id: int
    last_log_ts: int
    cwd: str | None


class Watcher:
    def __init__(
        self,
        *,
        db_path: Path = DEFAULT_DB,
        session_index_path: Path = DEFAULT_SESSION_INDEX,
        state_path: Path = DEFAULT_STATE,
        log_path: Path = DEFAULT_LOG,
        pid_path: Path = DEFAULT_PID,
        lock_path: Path = DEFAULT_LOCK,
        poll_seconds: float = DEFAULT_POLL_SECONDS,
        approval_grace_seconds: float = DEFAULT_APPROVAL_GRACE_SECONDS,
        dry_run: bool = False,
        from_now: bool = True,
    ) -> None:
        self.db_path = db_path.expanduser()
        self.session_index_path = session_index_path.expanduser()
        self.state_path = state_path.expanduser()
        self.log_path = log_path.expanduser()
        self.pid_path = pid_path.expanduser()
        self.lock_path = lock_path.expanduser()
        self.poll_seconds = poll_seconds
        self.approval_grace_seconds = approval_grace_seconds
        self.dry_run = dry_run
        self.from_now = from_now
        self.last_id = 0
        self.last_thread_id: str | None = None
        self.pending_calls: dict[str, PendingCall] = {}
        self.alerted_keys: set[str] = set()
        self.thread_titles: dict[str, str] = {}
        self.thread_cwds: dict[str, str] = {}
        self.thread_titles_loaded_at = 0.0
        self.stop_requested = False

    def run_forever(self) -> int:
        lock_handle = self.acquire_lock()
        if lock_handle is None:
            return 2
        self.write_pid()
        self.log("starting watcher")
        self.last_id = self.load_last_id()
        previous_sigterm = signal.getsignal(signal.SIGTERM)
        signal.signal(signal.SIGTERM, self.request_stop)
        try:
            while not self.stop_requested:
                try:
                    rows = self.fetch_rows_after(self.last_id)
                    now = time.monotonic()
                    for row in rows:
                        self.process_row(row, now)
                        self.last_id = max(self.last_id, row.id)
                    self.check_pending(now)
                    if rows:
                        self.save_last_id(self.last_id)
                except Exception as exc:  # Keep the watcher alive.
                    self.log(f"watcher error: {exc!r}")
                time.sleep(self.poll_seconds)
        except KeyboardInterrupt:
            self.log("stopping watcher")
            return 130
        finally:
            signal.signal(signal.SIGTERM, previous_sigterm)
            if self.stop_requested:
                self.log("stopping watcher")
            self.remove_pid()
            lock_handle.close()
        return 0

    def request_stop(self, _signum, _frame) -> None:
        self.stop_requested = True

    def load_last_id(self) -> int:
        if self.state_path.exists():
            try:
                data = json.loads(self.state_path.read_text())
                return int(data.get("last_id", 0))
            except Exception as exc:
                self.log(f"could not read state; starting from current max id: {exc!r}")
        if self.from_now:
            max_id = self.fetch_max_id()
            self.save_last_id(max_id)
            return max_id
        return 0

    def save_last_id(self, last_id: int) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"last_id": last_id, "updated_at": int(time.time())}
        self.state_path.write_text(json.dumps(payload, indent=2) + "\n")

    def fetch_max_id(self) -> int:
        if not self.db_path.exists():
            return 0
        uri = self.sqlite_uri()
        with sqlite3.connect(uri, uri=True, timeout=1) as conn:
            row = conn.execute("select coalesce(max(id), 0) from logs").fetchone()
        return int(row[0] or 0)

    def fetch_rows_after(self, last_id: int) -> list[LogRow]:
        if not self.db_path.exists():
            self.log(f"database missing: {self.db_path}")
            return []
        uri = self.sqlite_uri()
        with sqlite3.connect(uri, uri=True, timeout=1) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                select id, ts, ts_nanos, level, target, feedback_log_body, thread_id
                from logs
                where id > ?
                order by id asc
                limit 500
                """,
                (last_id,),
            ).fetchall()
        return [
            LogRow(
                id=int(row["id"]),
                ts=int(row["ts"]),
                ts_nanos=int(row["ts_nanos"]),
                level=str(row["level"]),
                target=str(row["target"]),
                body=str(row["feedback_log_body"] or ""),
                thread_id=row["thread_id"],
            )
            for row in rows
        ]

    def sqlite_uri(self) -> str:
        return f"file:{self.db_path}?mode=ro&cache=shared"

    def acquire_lock(self):
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_handle = self.lock_path.open("w")
        try:
            fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            pid = read_pid(self.pid_path)
            detail = f" pid {pid}" if pid else ""
            self.log(f"watcher already running{detail}")
            print(f"codex-notify-watch is already running{detail}", file=sys.stderr)
            lock_handle.close()
            return None
        lock_handle.write(str(os.getpid()))
        lock_handle.flush()
        return lock_handle

    def write_pid(self) -> None:
        self.pid_path.parent.mkdir(parents=True, exist_ok=True)
        self.pid_path.write_text(str(os.getpid()) + "\n")

    def remove_pid(self) -> None:
        try:
            if self.pid_path.exists() and read_pid(self.pid_path) == os.getpid():
                self.pid_path.unlink()
        except Exception as exc:
            self.log(f"could not remove pid file: {exc!r}")

    def process_row(self, row: LogRow, now: float) -> None:
        if row.thread_id:
            self.last_thread_id = row.thread_id
            cwd = self.extract_cwd(row.body)
            if cwd:
                self.thread_cwds[row.thread_id] = cwd

        if self.is_finish_event(row):
            thread_id = row.thread_id or self.last_thread_id
            label = self.thread_label(thread_id)
            key = f"finish:{thread_id}:{row.id}"
            self.alert_once(key, f"Codex: {label}", "Finished", FINISH_SOUND, thread_id)
            return

        if self.is_user_input_tool_call(row):
            thread_id = row.thread_id or self.last_thread_id
            label = self.thread_label(thread_id)
            key = f"user-input:{row.id}"
            self.alert_once(key, f"Codex: {label}", self.command_summary(row.body, "request_user_input"), USER_INPUT_SOUND, thread_id)
            return

        call = self.extract_function_call(row)
        if call:
            call_id, tool_name, summary = call
            if tool_name == "request_user_input":
                thread_id = row.thread_id or self.last_thread_id
                label = self.thread_label(thread_id)
                key = f"user-input:{call_id}"
                self.alert_once(key, f"Codex: {label}", summary, USER_INPUT_SOUND, thread_id)
                return
            if not self.should_track_approval_tool(tool_name):
                return
            existing = self.pending_calls.get(call_id)
            if existing:
                if summary != "Codex needs approval":
                    existing.summary = summary
                return
            self.pending_calls[call_id] = PendingCall(
                call_id=call_id,
                tool_name=tool_name,
                first_seen=now,
                thread_id=row.thread_id,
                summary=summary,
            )
            return

        call_id = self.extract_dispatched_call_id(row)
        if call_id and call_id in self.pending_calls:
            self.pending_calls[call_id].dispatched = True

    def is_finish_event(self, row: LogRow) -> bool:
        return (
            row.target == "codex_app_server::outgoing_message"
            and "app-server event: turn/completed" in row.body
        )

    def is_user_input_tool_call(self, row: LogRow) -> bool:
        return "ToolCall: request_user_input" in row.body

    def should_track_approval_tool(self, tool_name: str) -> bool:
        return tool_name in APPROVAL_TOOL_NAMES or tool_name.startswith("mcp__")

    def extract_function_call(self, row: LogRow) -> tuple[str, str, str] | None:
        body = row.body
        direct = FUNCTION_CALL_RE.search(body)
        if direct:
            tool_name, call_id = direct.group(1), direct.group(2)
            return call_id, tool_name, self.command_summary(body, tool_name)

        if not JSON_FUNCTION_CALL_RE.search(body):
            return None
        call_id_match = CALL_ID_RE.search(body)
        if not call_id_match:
            return None
        name_match = NAME_RE.search(body)
        tool_name = name_match.group(1) if name_match else "tool"
        return call_id_match.group(1), tool_name, self.command_summary(body, tool_name)

    def extract_dispatched_call_id(self, row: LogRow) -> str | None:
        body = row.body
        if "dispatch_tool_call" not in body and "dispatch_tool_call_with" not in body:
            return None
        match = re.search(r'call_id="([^"]+)"', body)
        return match.group(1) if match else None

    def command_summary(self, body: str, tool_name: str) -> str:
        if tool_name == "request_user_input":
            question = self.extract_question(body)
            if question:
                short = question if len(question) <= 90 else question[:87] + "..."
                return f"Needs your answer: {short}"
            return "Needs your answer"
        cmd = self.extract_cmd(body)
        if cmd:
            short = cmd if len(cmd) <= 90 else cmd[:87] + "..."
            return f"Needs permission: {short}"
        return f"Needs permission for {tool_name}"

    def extract_cmd(self, body: str) -> str | None:
        args_match = ARGS_RE.search(body)
        candidates: Iterable[str] = []
        if args_match:
            candidates = [args_match.group(1), body]
        else:
            candidates = [body]
        for candidate in candidates:
            cmd_match = CMD_RE.search(candidate)
            if cmd_match:
                return cmd_match.group(1).replace("\\\\", "\\")
        return None

    def extract_question(self, body: str) -> str | None:
        args_match = ARGS_RE.search(body)
        candidates: Iterable[str] = [body]
        if args_match:
            candidates = [args_match.group(1), body]
        for candidate in candidates:
            question_match = QUESTION_RE.search(candidate)
            if question_match:
                return question_match.group(1).replace("\\\\", "\\")
        return None

    def extract_cwd(self, body: str) -> str | None:
        match = CWD_RE.search(body)
        if not match:
            return None
        cwd = match.group(1).strip()
        return cwd or None

    def check_pending(self, now: float) -> None:
        expired: list[str] = []
        for call_id, call in self.pending_calls.items():
            if call.dispatched:
                expired.append(call_id)
                continue
            if call.alerted:
                if now - call.first_seen > 60:
                    expired.append(call_id)
                continue
            if now - call.first_seen >= self.approval_grace_seconds:
                key = f"approval:{call.call_id}"
                thread_id = call.thread_id or self.last_thread_id
                label = self.thread_label(thread_id)
                self.alert_once(key, f"Codex: {label}", call.summary, APPROVAL_SOUND, thread_id)
                call.alerted = True
        for call_id in expired:
            self.pending_calls.pop(call_id, None)

    def alert_once(self, key: str, title: str, message: str, sound: str, thread_id: str | None = None) -> None:
        if key in self.alerted_keys:
            return
        self.alerted_keys.add(key)
        self.log(f"alert {key}: {message}")
        if not self.dry_run:
            self.notify(key, title, message, sound, thread_id)

    def notify(self, key: str, title: str, message: str, sound: str, thread_id: str | None) -> None:
        if DEFAULT_MENU_BIN.exists() and self.write_menu_notification(key, title, message, sound, thread_id):
            return
        # Fire banner and sound without blocking. afplay blocks until the clip
        # finishes, so running it inline delayed the banner by ~1-2s.
        script = f'display notification {apple_quote(message)} with title {apple_quote(title)}'
        self.spawn_command(["/usr/bin/osascript", "-l", "AppleScript", "-e", script])
        sound_path = Path("/System/Library/Sounds") / f"{sound}.aiff"
        if sound_path.exists():
            self.spawn_command(["/usr/bin/afplay", str(sound_path)])

    def write_menu_notification(self, key: str, title: str, message: str, sound: str, thread_id: str | None) -> bool:
        try:
            DEFAULT_MENU_NOTIFY.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "id": f"{int(time.time() * 1000)}-{key}",
                "title": title,
                "message": message,
                "sound": sound,
                "thread_id": thread_id,
                "cwd": self.thread_cwds.get(thread_id or ""),
                "created_at": int(time.time()),
            }
            with DEFAULT_MENU_NOTIFY.open("a") as handle:
                handle.write(json.dumps(payload, separators=(",", ":")) + "\n")
            return True
        except Exception as exc:
            self.log(f"could not write menu notification: {exc!r}")
            return False

    def spawn_command(self, args: list[str]) -> None:
        try:
            subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as exc:
            self.log(f"notification command failed {shlex.join(args)}: {exc!r}")

    def thread_label(self, thread_id: str | None) -> str:
        if not thread_id:
            return "Unknown session"
        self.refresh_thread_titles()
        title = self.thread_titles.get(thread_id)
        if title:
            return title if len(title) <= 70 else title[:67] + "..."
        return f"Session {thread_id[:8]}"

    def refresh_thread_titles(self) -> None:
        now = time.monotonic()
        if now - self.thread_titles_loaded_at < 5:
            return
        self.thread_titles_loaded_at = now
        if not self.session_index_path.exists():
            return
        titles: dict[str, str] = {}
        try:
            with self.session_index_path.open() as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    thread_id = item.get("id")
                    title = item.get("thread_name")
                    if isinstance(thread_id, str) and isinstance(title, str) and title:
                        titles[thread_id] = title
        except Exception as exc:
            self.log(f"could not read session index: {exc!r}")
            return
        if titles:
            self.thread_titles = titles

    def log(self, message: str) -> None:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        line = f"{ts} {message}\n"
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a") as handle:
                handle.write(line)
        except Exception:
            sys.stderr.write(line)


def apple_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def read_pid(path: Path) -> int | None:
    try:
        return int(path.expanduser().read_text().strip())
    except Exception:
        return None


def pid_is_running(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def print_status(pid_path: Path) -> int:
    pid = read_pid(pid_path)
    if pid_is_running(pid):
        print(f"running pid={pid}")
        return 0
    if pid:
        print(f"not running; stale pid={pid}")
        return 1
    print("not running")
    return 1


def print_logs(args: argparse.Namespace) -> int:
    paths = [
        ("Codex SQLite DB", args.db),
        ("Codex session index", args.session_index),
        ("Watcher debug log", args.log),
        ("Watcher stdout log", DEFAULT_STDOUT_LOG),
        ("Watcher stderr log", DEFAULT_STDERR_LOG),
        ("Menu notification queue", DEFAULT_MENU_NOTIFY),
        ("Watcher state", args.state),
        ("Watcher PID", args.pid_file),
        ("Watcher lock", args.lock_file),
    ]
    for label, path in paths:
        expanded = path.expanduser()
        status = "exists" if expanded.exists() else "missing"
        print(f"{label}: {expanded} ({status})")
    return 0


def stop_running(pid_path: Path, log_path: Path) -> int:
    pid = read_pid(pid_path)
    if not pid_is_running(pid):
        print("not running")
        return 1
    os.kill(pid, signal.SIGTERM)
    deadline = time.time() + 5
    while time.time() < deadline:
        if not pid_is_running(pid):
            print(f"stopped pid={pid}")
            return 0
        time.sleep(0.1)
    print(f"sent stop signal to pid={pid}; process still appears to be running")
    try:
        with log_path.expanduser().open("a") as handle:
            handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} stop signal sent to pid={pid}\n")
    except Exception:
        pass
    return 2


def read_session_infos(path: Path, limit: int) -> list[SessionInfo]:
    if not path.expanduser().exists():
        return []
    sessions: dict[str, SessionInfo] = {}
    try:
        with path.expanduser().open() as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                thread_id = item.get("id")
                title = item.get("thread_name")
                updated_at = item.get("updated_at", "")
                if isinstance(thread_id, str) and isinstance(title, str) and title:
                    sessions[thread_id] = SessionInfo(thread_id, title, str(updated_at))
    except Exception as exc:
        print(f"could not read session index: {exc!r}", file=sys.stderr)
        return []
    ordered = sorted(sessions.values(), key=lambda item: item.updated_at, reverse=True)
    return ordered[:limit]


def fetch_session_rows(db_path: Path, thread_ids: list[str]) -> list[LogRow]:
    if not thread_ids or not db_path.expanduser().exists():
        return []
    placeholders = ",".join("?" for _ in thread_ids)
    uri = f"file:{db_path.expanduser()}?mode=ro&cache=shared"
    with sqlite3.connect(uri, uri=True, timeout=1) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"""
            select id, ts, ts_nanos, level, target, feedback_log_body, thread_id
            from logs
            where thread_id in ({placeholders})
            order by id asc
            """,
            thread_ids,
        ).fetchall()
    return [
        LogRow(
            id=int(row["id"]),
            ts=int(row["ts"]),
            ts_nanos=int(row["ts_nanos"]),
            level=str(row["level"]),
            target=str(row["target"]),
            body=str(row["feedback_log_body"] or ""),
            thread_id=row["thread_id"],
        )
        for row in rows
    ]


def session_statuses(args: argparse.Namespace) -> list[SessionStatus]:
    infos = read_session_infos(args.session_index, args.sessions_limit)
    if not infos:
        return []
    watcher = Watcher(
        db_path=args.db,
        session_index_path=args.session_index,
        state_path=args.state,
        log_path=args.log,
        pid_path=args.pid_file,
        lock_path=args.lock_file,
    )
    by_thread = {
        info.thread_id: {"last_log_id": 0, "last_log_ts": 0, "completed_id": 0, "pending": {}, "cwd": None}
        for info in infos
    }
    rows = fetch_session_rows(args.db, [info.thread_id for info in infos])
    for row in rows:
        if not row.thread_id or row.thread_id not in by_thread:
            continue
        state = by_thread[row.thread_id]
        state["last_log_id"] = max(int(state["last_log_id"]), row.id)
        state["last_log_ts"] = max(int(state["last_log_ts"]), row.ts)
        cwd = watcher.extract_cwd(row.body)
        if cwd:
            state["cwd"] = cwd
        if watcher.is_finish_event(row):
            state["completed_id"] = row.id
            state["pending"] = {}
            continue
        call = watcher.extract_function_call(row)
        if call:
            call_id, tool_name, _summary = call
            if watcher.should_track_approval_tool(tool_name):
                state["pending"][call_id] = row.ts
            continue
        dispatched = watcher.extract_dispatched_call_id(row)
        if dispatched:
            state["pending"].pop(dispatched, None)

    statuses: list[SessionStatus] = []
    now = int(time.time())
    for info in infos:
        state = by_thread[info.thread_id]
        fresh_pending = any(now - ts <= SESSION_PENDING_SECONDS for ts in state["pending"].values())
        fresh_activity = bool(state["last_log_ts"]) and now - int(state["last_log_ts"]) <= SESSION_ACTIVE_SECONDS
        if fresh_pending:
            status = "needs approval"
        elif fresh_activity:
            status = "running"
        elif state["last_log_id"]:
            status = "completed"
        else:
            status = "unknown"
        statuses.append(
            SessionStatus(
                thread_id=info.thread_id,
                title=info.title,
                status=status,
                updated_at=info.updated_at,
                last_log_id=int(state["last_log_id"]),
                last_log_ts=int(state["last_log_ts"]),
                cwd=state["cwd"],
            )
        )
    return statuses


def print_sessions(args: argparse.Namespace) -> int:
    statuses = session_statuses(args)
    if args.sessions_json:
        print(json.dumps([status.__dict__ for status in statuses], indent=2))
        return 0
    if not statuses:
        print("No Codex sessions found.")
        return 1
    width = min(max(len(status.title) for status in statuses), 48)
    for status in statuses:
        title = status.title if len(status.title) <= width else status.title[: width - 3] + "..."
        print(f"{title:<{width}}  {status.status}")
    return 0


def run_self_test() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        watcher = Watcher(
            dry_run=True,
            from_now=False,
            session_index_path=tmp_path / "session_index.jsonl",
            state_path=tmp_path / "state.json",
            log_path=tmp_path / "watch.log",
            pid_path=tmp_path / "watch.pid",
            lock_path=tmp_path / "watch.lock",
        )
        (tmp_path / "session_index.jsonl").write_text(
            json.dumps({"id": "thread-a", "thread_name": "Job notification alerts"}) + "\n"
        )
        now = time.monotonic()

        finish = LogRow(
            id=1,
            ts=0,
            ts_nanos=0,
            level="TRACE",
            target="codex_app_server::outgoing_message",
            body="app-server event: turn/completed targeted_connections=1",
            thread_id="thread-a",
        )
        watcher.process_row(finish, now)
        watcher.process_row(finish, now)
        assert len([k for k in watcher.alerted_keys if k.startswith("finish:")]) == 1

        function_waiting = LogRow(
            id=2,
            ts=0,
            ts_nanos=0,
            level="DEBUG",
            target="codex_core::stream_events_utils",
            body='Output item item=FunctionCall { name: "exec_command", call_id: "call_wait" }',
            thread_id="thread-a",
        )
        watcher.process_row(function_waiting, now)
        watcher.check_pending(now + DEFAULT_APPROVAL_GRACE_SECONDS + 0.1)
        assert "approval:call_wait" in watcher.alerted_keys

        function_fast = LogRow(
            id=3,
            ts=0,
            ts_nanos=0,
            level="DEBUG",
            target="codex_core::stream_events_utils",
            body='Output item item=FunctionCall { name: "exec_command", call_id: "call_fast" }',
            thread_id="thread-a",
        )
        dispatched_fast = LogRow(
            id=4,
            ts=0,
            ts_nanos=0,
            level="TRACE",
            target="log",
            body='dispatch_tool_call_with_terminal_outcome call_id="call_fast"',
            thread_id="thread-a",
        )
        watcher.process_row(function_fast, now)
        watcher.process_row(dispatched_fast, now + 0.1)
        watcher.check_pending(now + DEFAULT_APPROVAL_GRACE_SECONDS + 0.1)
        assert "approval:call_fast" not in watcher.alerted_keys

        user_input = LogRow(
            id=5,
            ts=0,
            ts_nanos=0,
            level="DEBUG",
            target="codex_core::stream_events_utils",
            body='Output item item=FunctionCall { name: "request_user_input", call_id: "call_input" }',
            thread_id="thread-a",
        )
        watcher.process_row(user_input, now)
        assert "user-input:call_input" in watcher.alerted_keys

    print("self-test passed")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Watch Codex logs for finish/approval alerts.")
    parser.add_argument("--version", action="store_true", help="Print version and exit.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="Path to Codex logs sqlite DB.")
    parser.add_argument(
        "--session-index",
        type=Path,
        default=DEFAULT_SESSION_INDEX,
        help="Path to Codex session_index.jsonl for human thread names.",
    )
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE, help="Path to watcher state JSON.")
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG, help="Path to watcher debug log.")
    parser.add_argument("--pid-file", type=Path, default=DEFAULT_PID, help="Path to watcher PID file.")
    parser.add_argument("--lock-file", type=Path, default=DEFAULT_LOCK, help="Path to watcher lock file.")
    parser.add_argument("--poll", type=float, default=DEFAULT_POLL_SECONDS, help="Polling interval seconds.")
    parser.add_argument(
        "--approval-grace",
        type=float,
        default=DEFAULT_APPROVAL_GRACE_SECONDS,
        help="Seconds to wait before treating a function call as approval-waiting.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Log alerts without sound/banner.")
    parser.add_argument("--from-beginning", action="store_true", help="Process old log rows too.")
    parser.add_argument("--self-test", action="store_true", help="Run built-in dry tests and exit.")
    parser.add_argument("--status", action="store_true", help="Print whether the watcher is running and exit.")
    parser.add_argument("--logs", action="store_true", help="Print useful Codex/watch log paths and exit.")
    parser.add_argument("--sessions", action="store_true", help="Print recent Codex session statuses and exit.")
    parser.add_argument("--sessions-json", action="store_true", help="Print recent Codex session statuses as JSON.")
    parser.add_argument("--sessions-limit", type=int, default=8, help="Number of recent sessions to show.")
    parser.add_argument("--stop", action="store_true", help="Stop the running watcher and exit.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.version:
        print(f"codex-notify-watch {VERSION}")
        return 0
    if args.self_test:
        return run_self_test()
    if args.status:
        return print_status(args.pid_file)
    if args.logs:
        return print_logs(args)
    if args.sessions or args.sessions_json:
        return print_sessions(args)
    if args.stop:
        return stop_running(args.pid_file, args.log)
    watcher = Watcher(
        db_path=args.db,
        session_index_path=args.session_index,
        state_path=args.state,
        log_path=args.log,
        pid_path=args.pid_file,
        lock_path=args.lock_file,
        poll_seconds=args.poll,
        approval_grace_seconds=args.approval_grace,
        dry_run=args.dry_run,
        from_now=not args.from_beginning,
    )
    return watcher.run_forever()


if __name__ == "__main__":
    raise SystemExit(main())
