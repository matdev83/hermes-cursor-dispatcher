#!/usr/bin/env python3
"""Safely delegate a UTF-8 prompt file to the Cursor CLI executable `agent`."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import select
import shutil
import signal
import stat
import subprocess
import tempfile
import threading
import time
from typing import Any, NoReturn, Sequence


DEFAULT_MAX_PROMPT_BYTES = 1024 * 1024
DEFAULT_MAX_OUTPUT_BYTES = 10 * 1024 * 1024
DEFAULT_CURSOR_MODEL = "grok-4.5-xhigh"
TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")


class DelegationError(Exception):
    def __init__(
        self,
        error_type: str,
        message: str,
        *,
        changes_may_exist: bool = False,
        recommended_action: str | None = None,
    ) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.message = message
        self.changes_may_exist = changes_may_exist
        self.recommended_action = recommended_action


class JsonArgumentParser(argparse.ArgumentParser):
    """Convert argparse failures into the wrapper's JSON error contract."""

    def error(self, message: str) -> NoReturn:
        raise DelegationError("configuration_error", f"Invalid wrapper arguments: {message}")


@dataclass(frozen=True)
class CursorDelegationConfig:
    executable: str
    prompt_file: Path
    workspace: Path
    timeout_seconds: float
    output_format: str
    mode: str | None
    allow_dirty_worktree: bool
    max_prompt_bytes: int
    max_stdout_bytes: int
    max_stderr_bytes: int
    allowed_root: Path | None = None
    isolate_worktree: bool = False
    worktree_root: Path = field(default_factory=lambda: Path(tempfile.gettempdir()))
    task_id: str | None = None
    sandbox: str | None = "enabled"
    model: str = DEFAULT_CURSOR_MODEL


@dataclass(frozen=True)
class GitSnapshot:
    repository_root: str
    head: str
    status_porcelain: str


@dataclass
class ProcessResult:
    exit_code: int | None
    timed_out: bool
    stdout: str
    stderr: str
    duration_seconds: float
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    stdout_log_path: str | None = None
    stderr_log_path: str | None = None


@dataclass
class BoundedCapture:
    path: Path
    maximum: int
    tail: bytearray = field(default_factory=bytearray)
    total_bytes: int = 0
    truncated: bool = False
    reader_error: str | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)


@dataclass
class DelegationResult:
    ok: bool
    exit_code: int | None
    timed_out: bool
    stdout: str
    stderr: str
    workspace: str
    duration_seconds: float
    command: list[str] = field(default_factory=list)
    parsed_output: Any = None
    starting_commit: str | None = None
    cursor_version: str | None = None
    task_id: str | None = None
    prompt_sha256: str | None = None
    prompt_bytes: int | None = None
    start_time: str | None = None
    end_time: str | None = None
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    stdout_log_path: str | None = None
    stderr_log_path: str | None = None
    git: dict[str, Any] = field(default_factory=dict)
    worktree: dict[str, Any] | None = None
    error: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value is not None}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_config(config: CursorDelegationConfig) -> None:
    if not math.isfinite(config.timeout_seconds) or config.timeout_seconds <= 0:
        raise DelegationError("configuration_error", "Timeout must be finite and greater than zero.")
    for name, value in (
        ("max_prompt_bytes", config.max_prompt_bytes),
        ("max_stdout_bytes", config.max_stdout_bytes),
        ("max_stderr_bytes", config.max_stderr_bytes),
    ):
        if value <= 0:
            raise DelegationError("configuration_error", f"{name} must be greater than zero.")
    if config.output_format not in {"text", "json", "stream-json"}:
        raise DelegationError("configuration_error", "Output format must be text, json, or stream-json.")
    if config.mode not in {None, "edit", "plan", "ask"}:
        raise DelegationError("configuration_error", "Mode must be edit, plan, or ask.")
    if config.sandbox not in {None, "enabled", "disabled"}:
        raise DelegationError("configuration_error", "Sandbox must be enabled or disabled.")
    if not config.model.strip():
        raise DelegationError("configuration_error", "Cursor model must not be empty.")
    if config.task_id is not None and not TASK_ID_RE.fullmatch(config.task_id):
        raise DelegationError(
            "configuration_error",
            "Task ID must contain only letters, digits, dots, underscores, and hyphens (maximum 80 characters).",
        )


def read_prompt(config: CursorDelegationConfig) -> tuple[str, int, str]:
    path = config.prompt_file.expanduser()
    try:
        flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0)
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise DelegationError("prompt_validation_error", f"Prompt path is not a regular file: {path}")
            encoded = handle.read(config.max_prompt_bytes + 1)
    except FileNotFoundError as exc:
        raise DelegationError("prompt_validation_error", f"Prompt file does not exist: {path}") from exc
    except OSError as exc:
        raise DelegationError("prompt_validation_error", f"Could not read prompt file {path}: {exc}") from exc
    if len(encoded) > config.max_prompt_bytes:
        raise DelegationError(
            "prompt_validation_error",
            f"Prompt exceeds the configured limit of {config.max_prompt_bytes} bytes; it was not truncated.",
        )
    try:
        prompt = encoded.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DelegationError("prompt_validation_error", f"Prompt file is not valid UTF-8: {exc}") from exc
    if not prompt.strip():
        raise DelegationError("prompt_validation_error", "Prompt file is empty or whitespace-only.")
    return prompt, len(encoded), hashlib.sha256(encoded).hexdigest()


def resolve_executable(executable: str) -> str:
    resolved = shutil.which(executable)
    if resolved is None:
        raise DelegationError("executable_not_found", f"Cursor CLI executable {executable!r} was not found in PATH.")
    path = Path(resolved).resolve()
    if not path.is_file() or not os.access(path, os.X_OK):
        raise DelegationError("executable_not_found", f"Cursor CLI executable is not executable: {path}")
    return str(path)


def controlled_environment() -> dict[str, str]:
    exact = {
        "PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "LC_CTYPE", "TERM",
        "TMPDIR", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "CURSOR_API_KEY",
        "GOPATH", "GOROOT", "GOMODCACHE", "CARGO_HOME", "RUSTUP_HOME", "NPM_CONFIG_USERCONFIG",
        "VIRTUAL_ENV", "JAVA_HOME", "GRADLE_USER_HOME", "MAVEN_OPTS",
    }
    return {key: os.environ[key] for key in exact if key in os.environ}


def resolve_git_executable() -> str:
    executable = shutil.which("git")
    if executable is None:
        raise DelegationError("git_inspection_error", "Git executable was not found in PATH.")
    return str(Path(executable).resolve())


def run_git(workspace: Path, args: Sequence[str]) -> str:
    try:
        completed = subprocess.run(
            [resolve_git_executable(), *args],
            cwd=workspace,
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise DelegationError("git_inspection_error", f"Could not run git {' '.join(args)}: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise DelegationError("git_inspection_error", f"git {' '.join(args)} failed: {detail}")
    return completed.stdout


def resolve_workspace(path: Path, allowed_root: Path | None) -> Path:
    workspace = path.expanduser().resolve()
    if not workspace.exists():
        raise DelegationError("workspace_validation_error", f"Workspace does not exist: {workspace}")
    if not workspace.is_dir():
        raise DelegationError("workspace_validation_error", f"Workspace is not a directory: {workspace}")
    if allowed_root is not None:
        root = allowed_root.expanduser().resolve()
        try:
            workspace.relative_to(root)
        except ValueError as exc:
            raise DelegationError("workspace_validation_error", f"Workspace {workspace} is outside allowed root {root}.") from exc
    return workspace


def ensure_within_allowed_root(path: Path, allowed_root: Path, label: str) -> None:
    resolved_path = path.expanduser().resolve()
    root = allowed_root.expanduser().resolve()
    try:
        resolved_path.relative_to(root)
    except ValueError as exc:
        raise DelegationError(
            "workspace_validation_error", f"{label} {resolved_path} is outside allowed root {root}."
        ) from exc


def inspect_git_workspace(workspace: Path) -> GitSnapshot:
    try:
        root = Path(run_git(workspace, ["rev-parse", "--show-toplevel"]).strip()).resolve()
        head = run_git(workspace, ["rev-parse", "HEAD"]).strip()
        status = run_git(workspace, ["status", "--porcelain=v1"]).rstrip("\n")
    except DelegationError as exc:
        if exc.error_type == "git_inspection_error":
            raise DelegationError("workspace_validation_error", f"Workspace is not an accessible Git repository: {exc.message}") from exc
        raise
    return GitSnapshot(str(root), head, status)


def assert_clean(snapshot: GitSnapshot, allow_dirty: bool) -> None:
    if snapshot.status_porcelain and not allow_dirty:
        raise DelegationError(
            "workspace_validation_error",
            "Workspace has uncommitted or untracked changes; pass --allow-dirty-worktree only after reviewing them.",
            recommended_action="Use an isolated worktree or explicitly allow the reviewed dirty state.",
        )


def generate_task_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"cursor-{stamp}-{os.urandom(3).hex()}"


def create_isolated_worktree(repository: Path, root: Path, task_id: str) -> tuple[Path, dict[str, Any]]:
    if not TASK_ID_RE.fullmatch(task_id):
        raise DelegationError("configuration_error", "Unsafe task ID for worktree creation.")
    root = root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    workspace = root / f"hermes-{task_id}"
    branch = f"hermes/{task_id}"
    if workspace.exists():
        raise DelegationError("workspace_validation_error", f"Worktree path already exists: {workspace}")
    try:
        completed = subprocess.run(
            [resolve_git_executable(), "worktree", "add", "-b", branch, str(workspace), "HEAD"],
            cwd=repository,
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise DelegationError("workspace_validation_error", f"Could not create isolated worktree: {exc}") from exc
    if completed.returncode != 0:
        raise DelegationError("workspace_validation_error", f"Could not create isolated worktree: {completed.stderr.strip()}")
    return workspace.resolve(), {
        "created": True,
        "retained": True,
        "path": str(workspace.resolve()),
        "branch": branch,
        "cleanup_commands": [
            ["git", "worktree", "remove", str(workspace.resolve())],
            ["git", "branch", "-D", branch],
        ],
    }


def build_cursor_argv(executable: str, config: CursorDelegationConfig, workspace: Path) -> list[str]:
    argv = [
        executable,
        "--print",
        "--output-format",
        config.output_format,
        "--model",
        config.model,
        "--trust",
        "--workspace",
        str(workspace),
    ]
    if config.sandbox is not None:
        argv.extend(["--sandbox", config.sandbox])
    if config.mode in {"plan", "ask"}:
        argv.extend(["--mode", config.mode])
    return argv


def get_cursor_version(executable: str, env: dict[str, str]) -> str | None:
    try:
        completed = subprocess.run(
            [executable, "--version"], shell=False, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=10, check=False, env=env,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = (completed.stdout or completed.stderr).strip()
    return value if completed.returncode == 0 and value else None


def descendant_pids(root_pid: int) -> set[int]:
    """Best-effort Linux descendant snapshot, including children that changed process groups."""
    proc = Path("/proc")
    if not proc.is_dir():
        return set()
    children: dict[int, list[int]] = {}
    try:
        entries = list(proc.iterdir())
    except OSError:
        return set()
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            stat_text = (entry / "stat").read_text(encoding="utf-8", errors="replace")
            fields = stat_text[stat_text.rfind(")") + 2 :].split()
            parent_pid = int(fields[1])
            children.setdefault(parent_pid, []).append(int(entry.name))
        except (OSError, ValueError, IndexError):
            continue
    found: set[int] = set()
    pending = list(children.get(root_pid, []))
    while pending:
        pid = pending.pop()
        if pid in found:
            continue
        found.add(pid)
        pending.extend(children.get(pid, []))
    return found


def signal_pids(pids: set[int], sig: signal.Signals) -> None:
    for pid in pids:
        try:
            os.kill(pid, sig)
        except (ProcessLookupError, PermissionError):
            pass


def terminate_process_group(process: subprocess.Popen[Any], grace_seconds: float = 0.2) -> None:
    # Snapshot descendants before the group leader is killed so children that
    # called setsid() can also be terminated on Linux.
    descendants = descendant_pids(process.pid)
    signal_pids(descendants, signal.SIGTERM)
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline:
        try:
            os.killpg(process.pid, 0)
            group_alive = True
        except ProcessLookupError:
            group_alive = False
        living_descendants: set[int] = set()
        for pid in descendants:
            try:
                os.kill(pid, 0)
                living_descendants.add(pid)
            except (ProcessLookupError, PermissionError):
                pass
        if not group_alive and not living_descendants:
            return
        descendants = living_descendants
        time.sleep(0.05)
    signal_pids(descendants, signal.SIGKILL)
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def drain_output(
    file_descriptor: int,
    capture: BoundedCapture,
    limit_event: threading.Event,
    stop_event: threading.Event,
) -> None:
    try:
        os.set_blocking(file_descriptor, False)
        with capture.path.open("wb", buffering=0) as log:
            while not stop_event.is_set():
                readable, _, _ = select.select([file_descriptor], [], [], 0.05)
                if not readable:
                    continue
                try:
                    chunk = os.read(file_descriptor, 65536)
                except BlockingIOError:
                    continue
                if not chunk:
                    return
                with capture.lock:
                    previous_total = capture.total_bytes
                    capture.total_bytes += len(chunk)
                    remaining = max(0, capture.maximum - previous_total)
                    if remaining:
                        log.write(chunk[:remaining])
                    capture.tail.extend(chunk)
                    if len(capture.tail) > capture.maximum:
                        del capture.tail[:-capture.maximum]
                    if capture.total_bytes > capture.maximum:
                        capture.truncated = True
                        limit_event.set()
    except (OSError, ValueError) as exc:
        with capture.lock:
            capture.reader_error = f"{type(exc).__name__}: {exc}"


def feed_prompt(file_descriptor: int, prompt: bytes, stop_event: threading.Event) -> None:
    try:
        os.set_blocking(file_descriptor, False)
        view = memoryview(prompt)
        while view and not stop_event.is_set():
            _, writable, _ = select.select([], [file_descriptor], [], 0.05)
            if not writable:
                continue
            try:
                written = os.write(file_descriptor, view)
            except BlockingIOError:
                continue
            view = view[written:]
    except (BrokenPipeError, OSError, ValueError):
        pass
    finally:
        try:
            os.close(file_descriptor)
        except OSError:
            pass


def finalize_capture(capture: BoundedCapture, reader_alive: bool) -> tuple[str, bool, str | None]:
    with capture.lock:
        data = bytes(capture.tail)
        truncated = capture.truncated or reader_alive
        reader_error = capture.reader_error
    if reader_error and not truncated:
        raise DelegationError(
            "unexpected_internal_error",
            f"Failed while capturing Cursor output: {reader_error}",
            changes_may_exist=True,
        )
    text = data.decode("utf-8", errors="replace")
    if len(text.encode("utf-8")) > capture.maximum:
        # One invalid byte can expand to the three-byte UTF-8 replacement
        # character. Restricting the source suffix to maximum // 3 therefore
        # guarantees a bounded encoded result without a quadratic trim loop.
        safe_source_bytes = capture.maximum // 3
        text = (
            data[-safe_source_bytes:].decode("utf-8", errors="replace")
            if safe_source_bytes
            else ""
        )
    if truncated:
        return text, True, str(capture.path)
    capture.path.unlink(missing_ok=True)
    return text, False, None


def run_cursor(
    argv: list[str],
    workspace: Path,
    timeout_seconds: float,
    env: dict[str, str],
    prompt: str,
    max_stdout_bytes: int,
    max_stderr_bytes: int,
    task_id: str,
) -> ProcessResult:
    started = time.monotonic()
    stdout_fd, stdout_name = tempfile.mkstemp(prefix=f"hermes-{task_id}-", suffix=".stdout.log")
    stderr_fd, stderr_name = tempfile.mkstemp(prefix=f"hermes-{task_id}-", suffix=".stderr.log")
    os.close(stdout_fd)
    os.close(stderr_fd)
    stdout_capture = BoundedCapture(Path(stdout_name), max_stdout_bytes)
    stderr_capture = BoundedCapture(Path(stderr_name), max_stderr_bytes)
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(
            argv,
            cwd=workspace,
            shell=False,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
            env=env,
            start_new_session=True,
        )
        if process.stdin is None or process.stdout is None or process.stderr is None:
            raise DelegationError(
                "unexpected_internal_error",
                "Cursor subprocess pipes were not initialized.",
                changes_may_exist=True,
            )
        limit_event = threading.Event()
        reader_stop_event = threading.Event()
        writer_stop_event = threading.Event()
        prompt_fd = os.dup(process.stdin.fileno())
        process.stdin.close()
        stdout_reader = threading.Thread(
            target=drain_output,
            args=(process.stdout.fileno(), stdout_capture, limit_event, reader_stop_event),
            daemon=True,
        )
        stderr_reader = threading.Thread(
            target=drain_output,
            args=(process.stderr.fileno(), stderr_capture, limit_event, reader_stop_event),
            daemon=True,
        )
        prompt_writer = threading.Thread(
            target=feed_prompt,
            args=(prompt_fd, prompt.encode("utf-8"), writer_stop_event),
            daemon=True,
        )
        stdout_reader.start()
        stderr_reader.start()
        prompt_writer.start()

        deadline = started + timeout_seconds
        termination_reason: str | None = None
        while process.poll() is None:
            if limit_event.is_set():
                termination_reason = "output_limit"
                terminate_process_group(process)
                break
            if time.monotonic() >= deadline:
                termination_reason = "timeout"
                terminate_process_group(process)
                break
            time.sleep(0.02)

        if process.poll() is None:
            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                process.kill()
                try:
                    process.wait(timeout=1.0)
                except subprocess.TimeoutExpired as exc:
                    raise DelegationError(
                        "timeout", "Cursor process could not be reaped after forced termination.",
                        changes_may_exist=True,
                    ) from exc

        # Cursor and repository tools must not leave detached-stdio children
        # behind after the direct CLI process exits. The new session guarantees
        # this group is scoped to the delegated execution.
        if termination_reason is None:
            terminate_process_group(process, grace_seconds=0.05)

        prompt_writer.join(timeout=0.2)
        if prompt_writer.is_alive():
            writer_stop_event.set()
            prompt_writer.join(timeout=0.2)
        stdout_reader.join(timeout=0.2)
        stderr_reader.join(timeout=0.2)
        if stdout_reader.is_alive() or stderr_reader.is_alive():
            terminate_process_group(process, grace_seconds=0.05)
            reader_stop_event.set()
            stdout_reader.join(timeout=0.2)
            stderr_reader.join(timeout=0.2)
        process.stdout.close()
        process.stderr.close()

        stdout, stdout_truncated, stdout_log = finalize_capture(stdout_capture, stdout_reader.is_alive())
        stderr, stderr_truncated, stderr_log = finalize_capture(stderr_capture, stderr_reader.is_alive())
        return ProcessResult(
            None if termination_reason == "timeout" else process.returncode,
            termination_reason == "timeout",
            stdout,
            stderr,
            time.monotonic() - started,
            stdout_truncated,
            stderr_truncated,
            stdout_log,
            stderr_log,
        )
    except Exception:
        if process is not None and process.poll() is None:
            terminate_process_group(process)
            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                process.kill()
                try:
                    process.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    pass
        stdout_capture.path.unlink(missing_ok=True)
        stderr_capture.path.unlink(missing_ok=True)
        raise


def parse_cursor_output(stdout: str, output_format: str) -> Any:
    if output_format == "text":
        return None
    try:
        if output_format == "stream-json":
            return [json.loads(line) for line in stdout.splitlines() if line.strip()]
        return json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise DelegationError(
            "invalid_cursor_json",
            "Cursor exited successfully but returned invalid JSON.",
            changes_may_exist=True,
            recommended_action="Inspect raw stdout and the workspace before retrying.",
        ) from exc


def collect_git_changes(workspace: Path, starting_status: str) -> dict[str, Any]:
    status = run_git(workspace, ["status", "--porcelain=v1"]).rstrip("\n")
    tracked = run_git(workspace, ["diff", "--name-only", "-z", "HEAD"]).split("\0")
    untracked = run_git(workspace, ["ls-files", "--others", "--exclude-standard", "-z"]).split("\0")
    changed_files = sorted({path for path in [*tracked, *untracked] if path})
    diff_stat = run_git(workspace, ["diff", "--stat", "HEAD"]).rstrip("\n")
    try:
        diff_check = subprocess.run(
            [resolve_git_executable(), "diff", "--check", "HEAD"],
            cwd=workspace,
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise DelegationError("git_inspection_error", f"Could not run git diff --check HEAD: {exc}") from exc
    return {
        "starting_status_porcelain": starting_status,
        "status_porcelain": status,
        "changed_files": changed_files,
        "changed_file_count": len(changed_files),
        "diff_stat": diff_stat,
        "diff_check_exit_code": diff_check.returncode,
        "diff_check_output": (diff_check.stdout + diff_check.stderr).strip(),
        "changes_may_exist": bool(changed_files or status),
    }


def error_payload(error: DelegationError) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": error.error_type,
        "message": error.message,
        "changes_may_exist": error.changes_may_exist,
    }
    if error.recommended_action:
        payload["recommended_action"] = error.recommended_action
    return payload


def delegate(config: CursorDelegationConfig) -> DelegationResult:
    validate_config(config)
    prompt, prompt_bytes, prompt_sha256 = read_prompt(config)
    executable = resolve_executable(config.executable)
    task_id = config.task_id or generate_task_id()
    source_workspace = resolve_workspace(config.workspace, config.allowed_root)
    source_snapshot = inspect_git_workspace(source_workspace)
    if config.allowed_root is not None:
        ensure_within_allowed_root(Path(source_snapshot.repository_root), config.allowed_root, "Repository root")
    assert_clean(source_snapshot, config.allow_dirty_worktree)

    worktree_info: dict[str, Any] | None = None
    workspace = source_workspace
    if config.isolate_worktree:
        if config.allowed_root is not None:
            candidate = config.worktree_root.expanduser().resolve() / f"hermes-{task_id}"
            ensure_within_allowed_root(candidate, config.allowed_root, "Isolated worktree")
        workspace, worktree_info = create_isolated_worktree(source_workspace, config.worktree_root, task_id)
    snapshot = inspect_git_workspace(workspace)
    assert_clean(snapshot, config.allow_dirty_worktree)

    env = controlled_environment()
    cursor_version = get_cursor_version(executable, env)
    argv = build_cursor_argv(executable, config, workspace)
    redacted_argv = [*argv, "<prompt via stdin omitted>"]
    started_at = utc_now()
    delegation_started = time.monotonic()
    try:
        process = run_cursor(
            argv,
            workspace,
            config.timeout_seconds,
            env,
            prompt,
            config.max_stdout_bytes,
            config.max_stderr_bytes,
            task_id,
        )
    except Exception as exc:
        failure = exc if isinstance(exc, DelegationError) else DelegationError(
            "unexpected_internal_error",
            f"Cursor execution failed unexpectedly: {type(exc).__name__}: {exc}",
            changes_may_exist=True,
            recommended_action=f"Inspect the retained workspace directly: {workspace}",
        )
        failure.changes_may_exist = True
        if failure.recommended_action is None:
            failure.recommended_action = f"Inspect the retained workspace directly: {workspace}"
        return DelegationResult(
            ok=False,
            exit_code=None,
            timed_out=failure.error_type == "timeout",
            stdout="",
            stderr="",
            workspace=str(workspace),
            duration_seconds=round(time.monotonic() - delegation_started, 3),
            command=redacted_argv,
            starting_commit=snapshot.head,
            cursor_version=cursor_version,
            task_id=task_id,
            prompt_sha256=prompt_sha256,
            prompt_bytes=prompt_bytes,
            start_time=started_at,
            end_time=utc_now(),
            git={
                "starting_status_porcelain": snapshot.status_porcelain,
                "changes_may_exist": True,
                "inspection_error": "Cursor execution did not return a normal process result.",
            },
            worktree=worktree_info,
            error=error_payload(failure),
        )
    ended_at = utc_now()
    git_error: DelegationError | None = None
    try:
        git_changes = collect_git_changes(workspace, snapshot.status_porcelain)
    except DelegationError as exc:
        git_error = DelegationError(
            "git_inspection_error",
            f"Cursor finished, but post-execution Git inspection failed: {exc.message}",
            changes_may_exist=True,
            recommended_action=f"Inspect the retained workspace directly: {workspace}",
        )
        git_changes = {
            "starting_status_porcelain": snapshot.status_porcelain,
            "inspection_error": exc.message,
            "changes_may_exist": True,
        }

    result = DelegationResult(
        ok=False,
        exit_code=process.exit_code,
        timed_out=process.timed_out,
        stdout=process.stdout,
        stderr=process.stderr,
        workspace=str(workspace),
        duration_seconds=round(process.duration_seconds, 3),
        command=redacted_argv,
        starting_commit=snapshot.head,
        cursor_version=cursor_version,
        task_id=task_id,
        prompt_sha256=prompt_sha256,
        prompt_bytes=prompt_bytes,
        start_time=started_at,
        end_time=ended_at,
        stdout_truncated=process.stdout_truncated,
        stderr_truncated=process.stderr_truncated,
        stdout_log_path=process.stdout_log_path,
        stderr_log_path=process.stderr_log_path,
        git=git_changes,
        worktree=worktree_info,
    )
    if git_error is not None:
        result.error = error_payload(git_error)
        return result
    if process.timed_out:
        result.error = error_payload(DelegationError(
            "timeout",
            f"Cursor CLI exceeded the configured timeout of {config.timeout_seconds:g} seconds.",
            changes_may_exist=git_changes["changes_may_exist"],
            recommended_action="Inspect the retained workspace before retrying.",
        ))
        return result
    if process.stdout_truncated or process.stderr_truncated:
        result.error = error_payload(DelegationError(
            "output_limit_exceeded",
            "Cursor output exceeded a configured capture limit; bounded output was retained in log files.",
            changes_may_exist=git_changes["changes_may_exist"],
            recommended_action="Inspect the bounded output logs and workspace before deciding whether to retry.",
        ))
        return result
    if process.exit_code != 0:
        result.error = error_payload(DelegationError(
            "cursor_exit_error",
            "Cursor CLI exited with a non-zero status.",
            changes_may_exist=git_changes["changes_may_exist"],
            recommended_action="Inspect stdout, stderr, and the retained workspace.",
        ))
        return result
    try:
        result.parsed_output = parse_cursor_output(process.stdout, config.output_format)
    except DelegationError as exc:
        exc.changes_may_exist = git_changes["changes_may_exist"]
        result.error = error_payload(exc)
        return result
    result.ok = True
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = JsonArgumentParser(description=__doc__)
    parser.add_argument("--prompt-file", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--timeout", dest="timeout_seconds", type=float, default=3600)
    parser.add_argument("--output-format", choices=("text", "json", "stream-json"), default="json")
    parser.add_argument("--mode", choices=("edit", "plan", "ask"), default="edit")
    parser.add_argument("--executable", default="agent")
    parser.add_argument("--allow-dirty-worktree", action="store_true")
    parser.add_argument("--max-prompt-bytes", type=int, default=DEFAULT_MAX_PROMPT_BYTES)
    parser.add_argument("--max-stdout-bytes", type=int, default=DEFAULT_MAX_OUTPUT_BYTES)
    parser.add_argument("--max-stderr-bytes", type=int, default=DEFAULT_MAX_OUTPUT_BYTES)
    parser.add_argument("--allowed-root", type=Path)
    parser.add_argument("--isolate-worktree", action="store_true")
    parser.add_argument("--worktree-root", type=Path, default=Path(tempfile.gettempdir()))
    parser.add_argument("--task-id")
    parser.add_argument("--sandbox", choices=("enabled", "disabled"), default="enabled")
    parser.add_argument("--model", default=DEFAULT_CURSOR_MODEL)
    return parser


def config_from_args(args: argparse.Namespace) -> CursorDelegationConfig:
    return CursorDelegationConfig(
        executable=args.executable,
        prompt_file=args.prompt_file,
        workspace=args.workspace,
        timeout_seconds=args.timeout_seconds,
        output_format=args.output_format,
        mode=args.mode,
        allow_dirty_worktree=args.allow_dirty_worktree,
        max_prompt_bytes=args.max_prompt_bytes,
        max_stdout_bytes=args.max_stdout_bytes,
        max_stderr_bytes=args.max_stderr_bytes,
        allowed_root=args.allowed_root,
        isolate_worktree=args.isolate_worktree,
        worktree_root=args.worktree_root,
        task_id=args.task_id,
        sandbox=args.sandbox,
        model=args.model,
    )


def main(argv: Sequence[str] | None = None) -> int:
    workspace = ""
    try:
        args = build_parser().parse_args(argv)
        workspace = str(args.workspace.expanduser())
        result = delegate(config_from_args(args))
    except DelegationError as exc:
        result = DelegationResult(
            ok=False, exit_code=None, timed_out=False, stdout="", stderr="",
            workspace=workspace, duration_seconds=0.0, error=error_payload(exc),
        )
    except Exception as exc:  # The stdout contract must survive unexpected failures.
        result = DelegationResult(
            ok=False, exit_code=None, timed_out=False, stdout="", stderr="",
            workspace=workspace, duration_seconds=0.0,
            error=error_payload(DelegationError(
                "unexpected_internal_error", f"Unexpected internal error: {type(exc).__name__}: {exc}",
                changes_may_exist=False,
                recommended_action="Inspect wrapper diagnostics and configuration before retrying.",
            )),
        )
    print(json.dumps(result.to_dict(), ensure_ascii=False, separators=(",", ":")))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
