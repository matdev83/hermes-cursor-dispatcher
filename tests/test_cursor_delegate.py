from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import textwrap
import time

import pytest


SKILL_DIR = Path(__file__).resolve().parents[1]
WRAPPER = SKILL_DIR / "cursor_delegate.py"


def run(
    *args: str,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: float = 30,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(WRAPPER), *args],
        cwd=cwd,
        env=env,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
        timeout=timeout,
    )


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, text=True, encoding="utf-8", capture_output=True, check=False
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def make_repo(path: Path, *, space: bool = False) -> Path:
    repo = path / ("repository with spaces" if space else "repo")
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "tests@example.invalid")
    git(repo, "config", "user.name", "Cursor Delegate Tests")
    (repo / "README.md").write_text("baseline\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-qm", "baseline")
    return repo


def make_fake_agent(bin_dir: Path, body: str) -> Path:
    bin_dir.mkdir(parents=True, exist_ok=True)
    executable = bin_dir / "agent"
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, shutil, subprocess, sys, time\n"
        "if '--version' in sys.argv[1:]: print('fake-agent 1.0'); raise SystemExit(0)\n" + textwrap.dedent(body),
        encoding="utf-8",
    )
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    return executable


def invoke(
    tmp_path: Path,
    prompt: str,
    *,
    agent_body: str | None = None,
    workspace: Path | None = None,
    output_format: str = "json",
    extra: tuple[str, ...] = (),
) -> tuple[subprocess.CompletedProcess[str], dict[str, object], Path]:
    repo = workspace or make_repo(tmp_path)
    prompt_file = tmp_path / "task.md"
    prompt_file.write_text(prompt, encoding="utf-8")
    capture = tmp_path / "captured.json"
    default_body = f"""
prompt = sys.stdin.read()
pathlib.Path({str(capture)!r}).write_text(json.dumps({{'argv': sys.argv[1:], 'stdin': prompt}}, ensure_ascii=False), encoding='utf-8')
print(json.dumps({{'received': prompt}}, ensure_ascii=False))
"""
    bin_dir = tmp_path / "bin"
    make_fake_agent(bin_dir, agent_body or default_body)
    env = os.environ.copy()
    env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
    completed = run(
        "--prompt-file", str(prompt_file),
        "--workspace", str(repo),
        "--timeout", "5",
        "--mode", "edit",
        "--output-format", output_format,
        *extra,
        env=env,
    )
    assert completed.stdout.strip(), completed.stderr
    return completed, json.loads(completed.stdout), capture


def assert_prompt_round_trip(tmp_path: Path, prompt: str) -> None:
    completed, result, capture = invoke(tmp_path, prompt)
    assert completed.returncode == 0, completed.stderr
    assert result["ok"] is True
    captured = json.loads(capture.read_text(encoding="utf-8"))
    assert captured["stdin"] == prompt
    assert prompt not in captured["argv"]
    assert captured["argv"][captured["argv"].index("--model") + 1] == "grok-4.5-xhigh"
    assert result["command"][-1] == "<prompt via stdin omitted>"
    assert result["prompt_bytes"] == len(prompt.encode("utf-8"))


def test_multiline_prompt_preserved(tmp_path: Path) -> None:
    assert_prompt_round_trip(tmp_path, "Line one\nLine two\n\nLine four\n")


def test_shell_metacharacters_are_literal_and_not_executed(tmp_path: Path) -> None:
    marker = tmp_path / "should-not-exist"
    prompt = "\n".join([
        "$HOME", "${TOKEN}", f"$(touch {marker})", "`uname -a`", "&&", "||", ">", "<", "|", "*", "?",
    ])
    assert_prompt_round_trip(tmp_path, prompt)
    assert not marker.exists()


def test_quotes_and_backslashes_preserved(tmp_path: Path) -> None:
    assert_prompt_round_trip(tmp_path, '\"It\'s quoted\"\n\'It is also quoted\'\nC:\\Users\\Example\n\\\\server\\share')


def test_markdown_code_fences_and_structured_data_preserved(tmp_path: Path) -> None:
    prompt = '''# Task

```go
func Example() string {
    return `raw string with $HOME and "quotes"`
}
```

```json
{"command":"$(do-not-execute)","template":"${DO_NOT_EXPAND}"}
```

```yaml
value: "a | b > c"
```
'''
    assert_prompt_round_trip(tmp_path, prompt)


def test_unicode_preserved(tmp_path: Path) -> None:
    assert_prompt_round_trip(tmp_path, "Zażółć gęślą jaźń\n日本語\n🙂\n")


@pytest.mark.parametrize("prompt", ["", " \n\t "])
def test_empty_prompt_rejected_before_agent(tmp_path: Path, prompt: str) -> None:
    completed, result, capture = invoke(tmp_path, prompt)
    assert completed.returncode != 0
    assert result["error"]["type"] == "prompt_validation_error"
    assert not capture.exists()


def test_missing_required_arguments_still_returns_json(tmp_path: Path) -> None:
    completed = run()
    assert completed.returncode != 0
    result = json.loads(completed.stdout)
    assert result["error"]["type"] == "configuration_error"


@pytest.mark.parametrize("value", ["nan", "inf", "-inf"])
def test_non_finite_timeout_rejected_before_agent(tmp_path: Path, value: str) -> None:
    completed, result, capture = invoke(tmp_path, "task", extra=("--timeout", value))
    assert completed.returncode != 0
    assert result["error"]["type"] == "configuration_error"
    assert not capture.exists()


def test_missing_prompt_file_returns_structured_error(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    completed = run("--prompt-file", str(tmp_path / "missing.md"), "--workspace", str(repo))
    result = json.loads(completed.stdout)
    assert completed.returncode != 0
    assert result["error"]["type"] == "prompt_validation_error"


def test_non_regular_prompt_is_rejected_without_blocking(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    fifo = tmp_path / "prompt.fifo"
    os.mkfifo(fifo)
    started = time.monotonic()
    completed = run("--prompt-file", str(fifo), "--workspace", str(repo))
    elapsed = time.monotonic() - started
    result = json.loads(completed.stdout)
    assert result["error"]["type"] == "prompt_validation_error"
    assert elapsed < 1.0


def test_invalid_utf8_prompt_returns_structured_error(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    prompt = tmp_path / "bad.md"
    prompt.write_bytes(b"\xff\xfe")
    completed = run("--prompt-file", str(prompt), "--workspace", str(repo))
    result = json.loads(completed.stdout)
    assert result["error"]["type"] == "prompt_validation_error"


def test_missing_workspace_returns_structured_error(tmp_path: Path) -> None:
    prompt = tmp_path / "task.md"
    prompt.write_text("task", encoding="utf-8")
    completed = run("--prompt-file", str(prompt), "--workspace", str(tmp_path / "missing"))
    result = json.loads(completed.stdout)
    assert result["error"]["type"] == "workspace_validation_error"


def test_missing_agent_executable(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    prompt = tmp_path / "task.md"
    prompt.write_text("task", encoding="utf-8")
    env = os.environ.copy()
    env["PATH"] = str(tmp_path / "empty-bin")
    completed = run("--prompt-file", str(prompt), "--workspace", str(repo), env=env)
    result = json.loads(completed.stdout)
    assert result["error"]["type"] == "executable_not_found"


def test_nonzero_cursor_exit_preserves_output(tmp_path: Path) -> None:
    body = """
print('partial stdout')
print('cursor failed', file=sys.stderr)
sys.exit(7)
"""
    completed, result, _ = invoke(tmp_path, "task", agent_body=body, output_format="text")
    assert completed.returncode != 0
    assert result["ok"] is False
    assert result["exit_code"] == 7
    assert result["error"]["type"] == "cursor_exit_error"
    assert "partial stdout" in result["stdout"]
    assert "cursor failed" in result["stderr"]


def test_timeout_kills_process_group(tmp_path: Path) -> None:
    marker = tmp_path / "orphan-marker"
    child_code = f"import time, pathlib; time.sleep(1.5); pathlib.Path({str(marker)!r}).write_text('orphan')"
    body = f"""
subprocess.Popen([sys.executable, '-c', {child_code!r}])
print('started', flush=True)
time.sleep(10)
"""
    completed, result, _ = invoke(tmp_path, "task", agent_body=body, output_format="text", extra=("--timeout", "0.25"))
    assert completed.returncode != 0
    assert result["timed_out"] is True
    assert result["error"]["type"] == "timeout", json.dumps(result, indent=2)
    assert "started" in result["stdout"]
    time.sleep(1.75)
    assert not marker.exists()


def test_timeout_is_bounded_when_descendant_escapes_process_group(tmp_path: Path) -> None:
    marker = tmp_path / "escaped-orphan-marker"
    child_code = (
        "import os,pathlib,time; os.setsid(); time.sleep(1); "
        f"pathlib.Path({str(marker)!r}).write_text('orphan')"
    )
    body = f"""
subprocess.Popen([sys.executable, '-c', {child_code!r}])
print('started escaped child', flush=True)
time.sleep(10)
"""
    started = time.monotonic()
    completed, result, _ = invoke(
        tmp_path, "task", agent_body=body, output_format="text", extra=("--timeout", "0.25")
    )
    elapsed = time.monotonic() - started
    assert completed.returncode != 0
    assert result["error"]["type"] == "timeout", json.dumps(result, indent=2)
    assert elapsed < 2.5
    time.sleep(1.1)
    assert not marker.exists()


def test_timeout_is_bounded_with_blocked_large_prompt_writer(tmp_path: Path) -> None:
    child_code = "import os,time; os.setsid(); time.sleep(4)"
    body = f"""
subprocess.Popen([sys.executable, '-c', {child_code!r}])
print('not reading stdin', flush=True)
time.sleep(10)
"""
    started = time.monotonic()
    completed, result, _ = invoke(
        tmp_path,
        "X" * 900_000,
        agent_body=body,
        output_format="text",
        extra=("--timeout", "0.25"),
    )
    elapsed = time.monotonic() - started
    assert completed.returncode != 0
    assert result["error"]["type"] == "timeout", json.dumps(result, indent=2)
    assert elapsed < 2.5


def test_successful_run_kills_detached_stdio_child_in_same_process_group(tmp_path: Path) -> None:
    marker = tmp_path / "orphan-marker"
    child_code = f"import pathlib,time; time.sleep(1); pathlib.Path({str(marker)!r}).write_text('orphan')"
    body = f"""
prompt = sys.stdin.read()
subprocess.Popen(
    [sys.executable, '-c', {child_code!r}],
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    close_fds=True,
)
print(json.dumps({{'received': prompt}}))
"""
    completed, result, _ = invoke(tmp_path, "task", agent_body=body)
    assert completed.returncode == 0, json.dumps(result, indent=2)
    time.sleep(1.25)
    assert not marker.exists()


def test_invalid_json_output_retains_raw_stdout(tmp_path: Path) -> None:
    completed, result, _ = invoke(tmp_path, "task", agent_body="print('{bad json')")
    assert completed.returncode != 0
    assert result["error"]["type"] == "invalid_cursor_json"
    assert result["stdout"].strip() == "{bad json"


def test_large_prompt_preserved(tmp_path: Path) -> None:
    prompt = ("line $HOME `literal` 日本語 🙂\n" * 40_000)[:1_500_000]
    completed, result, capture = invoke(tmp_path, prompt, extra=("--max-prompt-bytes", "2097152"))
    assert completed.returncode == 0, completed.stderr
    assert result["ok"] is True
    captured = json.loads(capture.read_text(encoding="utf-8"))
    assert captured["stdin"] == prompt


def test_oversized_prompt_rejected_not_truncated(tmp_path: Path) -> None:
    completed, result, capture = invoke(tmp_path, "x" * 2049, extra=("--max-prompt-bytes", "2048"))
    assert completed.returncode != 0
    assert result["error"]["type"] == "prompt_validation_error"
    assert not capture.exists()


def test_dirty_worktree_rejected_by_default(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    (repo / "dirty.txt").write_text("dirty", encoding="utf-8")
    completed, result, capture = invoke(tmp_path, "task", workspace=repo)
    assert completed.returncode != 0
    assert result["error"]["type"] == "workspace_validation_error"
    assert not capture.exists()


def test_dirty_worktree_allowed_with_explicit_override(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    (repo / "dirty.txt").write_text("dirty", encoding="utf-8")
    completed, result, capture = invoke(tmp_path, "task", workspace=repo, extra=("--allow-dirty-worktree",))
    assert completed.returncode == 0
    assert result["ok"] is True
    assert capture.exists()
    assert "dirty.txt" in result["git"]["starting_status_porcelain"]


def test_option_like_prompt_is_not_parsed_as_cli_options(tmp_path: Path) -> None:
    prompt = "--help\n--version\n--output-format text"
    completed, result, capture = invoke(tmp_path, prompt)
    assert completed.returncode == 0
    captured = json.loads(capture.read_text(encoding="utf-8"))
    assert captured["stdin"] == prompt
    assert "--help" not in captured["argv"]
    assert result["parsed_output"]["received"] == prompt


def test_workspace_path_with_spaces(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, space=True)
    completed, result, _ = invoke(tmp_path, "task", workspace=repo)
    assert completed.returncode == 0
    assert result["workspace"] == str(repo.resolve())


def test_output_truncation_preserves_bounded_logs(tmp_path: Path) -> None:
    body = """
print('A' * 5000)
print('B' * 5000, file=sys.stderr)
"""
    completed, result, _ = invoke(
        tmp_path, "task", agent_body=body, output_format="text",
        extra=("--max-stdout-bytes", "1024", "--max-stderr-bytes", "1024"),
    )
    assert completed.returncode != 0
    assert result["error"]["type"] == "output_limit_exceeded"
    assert result["stdout_truncated"] is True
    assert result["stderr_truncated"] is True
    assert len(result["stdout"].encode("utf-8")) <= 1024
    assert len(result["stderr"].encode("utf-8")) <= 1024
    assert Path(result["stdout_log_path"]).read_text(encoding="utf-8").startswith("A")
    assert Path(result["stderr_log_path"]).read_text(encoding="utf-8").startswith("B")
    Path(result["stdout_log_path"]).unlink()
    Path(result["stderr_log_path"]).unlink()


def test_output_limit_terminates_run_and_bounds_retained_log(tmp_path: Path) -> None:
    body = """
chunk = 'X' * 65536
while True:
    print(chunk, flush=True)
"""
    started = time.monotonic()
    completed, result, _ = invoke(
        tmp_path,
        "task",
        agent_body=body,
        output_format="text",
        extra=("--max-stdout-bytes", "4096", "--timeout", "10"),
    )
    elapsed = time.monotonic() - started
    assert completed.returncode != 0
    assert result["error"]["type"] == "output_limit_exceeded"
    assert result["stdout_truncated"] is True
    assert len(result["stdout"].encode("utf-8")) <= 4096
    assert Path(result["stdout_log_path"]).stat().st_size <= 4096
    assert elapsed < 2.5
    Path(result["stdout_log_path"]).unlink()


def test_invalid_utf8_output_finalization_is_linear_and_bounded(tmp_path: Path) -> None:
    body = "sys.stdout.buffer.write(b'\\xff' * 65536); sys.stdout.buffer.flush()"
    started = time.monotonic()
    completed, result, _ = invoke(
        tmp_path,
        "task",
        agent_body=body,
        output_format="text",
        extra=("--max-stdout-bytes", "65536"),
    )
    elapsed = time.monotonic() - started
    assert completed.returncode == 0, json.dumps(result, indent=2)
    assert len(result["stdout"].encode("utf-8")) <= 65536
    assert elapsed < 1.5

    tiny_path = tmp_path / "tiny"
    tiny_path.mkdir()
    tiny_completed, tiny_result, _ = invoke(
        tiny_path,
        "task",
        agent_body="sys.stdout.buffer.write(b'\\xff'); sys.stdout.buffer.flush()",
        output_format="text",
        extra=("--max-stdout-bytes", "1"),
    )
    assert tiny_completed.returncode == 0, json.dumps(tiny_result, indent=2)
    assert len(tiny_result["stdout"].encode("utf-8")) <= 1


def test_controlled_environment_does_not_forward_arbitrary_secret_prefixes(tmp_path: Path) -> None:
    capture = tmp_path / "env.json"
    body = f"""
pathlib.Path({str(capture)!r}).write_text(json.dumps(dict(os.environ)), encoding='utf-8')
print(json.dumps({{'done': True}}))
"""
    repo = make_repo(tmp_path)
    prompt = tmp_path / "task.md"
    prompt.write_text("task", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    make_fake_agent(bin_dir, body)
    env = os.environ.copy()
    env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
    env["NODE_AUTH_TOKEN"] = "must-not-leak"  # pragma: allowlist secret -- synthetic test value
    env["NPM_PRIVATE_TOKEN"] = "must-not-leak"  # pragma: allowlist secret -- synthetic test value
    env["GIT_EVIL_SECRET"] = "must-not-leak"  # pragma: allowlist secret -- synthetic test value
    env["PYTHONSTARTUP"] = "/tmp/must-not-run.py"
    completed = run("--prompt-file", str(prompt), "--workspace", str(repo), env=env)
    assert completed.returncode == 0
    delegated_env = json.loads(capture.read_text(encoding="utf-8"))
    assert delegated_env["HOME"] == env["HOME"]
    assert "PATH" in delegated_env
    assert "NODE_AUTH_TOKEN" not in delegated_env
    assert "NPM_PRIVATE_TOKEN" not in delegated_env
    assert "GIT_EVIL_SECRET" not in delegated_env
    assert "PYTHONSTARTUP" not in delegated_env


def test_post_run_git_failure_retains_workspace_and_warns_changes_may_exist(tmp_path: Path) -> None:
    body = """
pathlib.Path('changed.txt').write_text('changed\\n', encoding='utf-8')
shutil.rmtree('.git')
print(json.dumps({'done': True}))
"""
    repo = make_repo(tmp_path)
    completed, result, _ = invoke(tmp_path, "task", workspace=repo, agent_body=body)
    assert completed.returncode != 0
    assert result["error"]["type"] == "git_inspection_error"
    assert result["error"]["changes_may_exist"] is True
    assert result["workspace"] == str(repo.resolve())
    assert (repo / "changed.txt").exists()


def test_isolated_post_run_git_failure_reports_retained_worktree(tmp_path: Path) -> None:
    body = """
pathlib.Path('changed.txt').write_text('changed\\n', encoding='utf-8')
git_path = pathlib.Path('.git')
git_path.unlink() if git_path.is_file() else shutil.rmtree(git_path)
print(json.dumps({'done': True}))
"""
    repo = make_repo(tmp_path)
    completed, result, _ = invoke(
        tmp_path,
        "task",
        workspace=repo,
        agent_body=body,
        extra=(
            "--isolate-worktree", "--worktree-root", str(tmp_path / "worktrees"),
            "--task-id", "cursor-post-git-failure",
        ),
    )
    retained = Path(result["workspace"])
    assert completed.returncode != 0
    assert result["error"]["type"] == "git_inspection_error"
    assert result["error"]["changes_may_exist"] is True
    assert retained != repo
    assert retained.exists()
    assert (retained / "changed.txt").exists()
    assert result["worktree"]["retained"] is True


def test_git_metadata_and_changes_are_collected(tmp_path: Path) -> None:
    body = """
pathlib.Path('changed.txt').write_text('made by cursor\\n', encoding='utf-8')
print(json.dumps({'done': True}))
"""
    completed, result, _ = invoke(tmp_path, "change file", agent_body=body)
    assert completed.returncode == 0
    assert result["starting_commit"]
    assert "changed.txt" in result["git"]["changed_files"]
    assert "changed.txt" in result["git"]["status_porcelain"]
    assert result["git"]["changes_may_exist"] is True


def test_git_metadata_includes_staged_and_unusual_filenames(tmp_path: Path) -> None:
    strange_name = "line\nbreak -> name.txt"
    body = f"""
pathlib.Path({str(strange_name)!r}).write_text('content\\n', encoding='utf-8')
subprocess.run(['git', 'add', {str(strange_name)!r}], check=True)
print(json.dumps({{'done': True}}))
"""
    completed, result, _ = invoke(tmp_path, "task", agent_body=body)
    assert completed.returncode == 0
    assert result["git"]["changed_files"] == [strange_name]
    assert result["git"]["diff_check_exit_code"] == 0


def test_allowed_root_rejects_workspace_outside_boundary(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    completed, result, _ = invoke(tmp_path, "task", workspace=repo, extra=("--allowed-root", str(tmp_path / "elsewhere")))
    assert completed.returncode != 0
    assert result["error"]["type"] == "workspace_validation_error"


def test_allowed_root_rejects_repository_root_outside_boundary(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    workspace = repo / "inside"
    workspace.mkdir()
    completed, result, _ = invoke(
        tmp_path, "task", workspace=workspace,
        extra=("--allowed-root", str(workspace)),
    )
    assert completed.returncode != 0
    assert result["error"]["type"] == "workspace_validation_error"


def test_allowed_root_rejects_isolated_worktree_destination_outside_boundary(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    completed, result, _ = invoke(
        tmp_path, "task", workspace=repo,
        extra=(
            "--allowed-root", str(repo), "--isolate-worktree",
            "--worktree-root", str(tmp_path / "outside"),
        ),
    )
    assert completed.returncode != 0
    assert result["error"]["type"] == "workspace_validation_error"
    assert not (tmp_path / "outside").exists()


def test_isolated_worktree_creation_and_retention(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    prompt = tmp_path / "task.md"
    prompt.write_text("make a change", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    make_fake_agent(bin_dir, "pathlib.Path('isolated.txt').write_text('yes\\n'); print(json.dumps({'done': True}))")
    env = os.environ.copy()
    env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
    worktree_root = tmp_path / "worktrees"
    completed = run(
        "--prompt-file", str(prompt), "--workspace", str(repo), "--isolate-worktree",
        "--worktree-root", str(worktree_root), "--task-id", "cursor-test-abc123",
        env=env,
    )
    result = json.loads(completed.stdout)
    assert completed.returncode == 0, completed.stderr
    delegated_workspace = Path(result["workspace"])
    assert delegated_workspace != repo
    assert delegated_workspace.is_dir()
    assert (delegated_workspace / "isolated.txt").exists()
    assert result["worktree"]["retained"] is True
    assert result["worktree"]["branch"] == "hermes/cursor-test-abc123"


def test_invalid_task_id_rejected(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    prompt = tmp_path / "task.md"
    prompt.write_text("task", encoding="utf-8")
    completed = run(
        "--prompt-file", str(prompt), "--workspace", str(repo), "--isolate-worktree",
        "--task-id", "../../unsafe",
    )
    result = json.loads(completed.stdout)
    assert result["error"]["type"] == "configuration_error"


@pytest.mark.cursor_integration
@pytest.mark.skipif(os.environ.get("RUN_CURSOR_INTEGRATION_TESTS") != "1", reason="explicit opt-in required")
def test_real_cursor_can_make_deterministic_change(tmp_path: Path) -> None:
    real_agent = shutil.which("agent")
    assert real_agent, "agent must be installed"
    repo = make_repo(tmp_path)
    (repo / "value.txt").write_text("before\n", encoding="utf-8")
    git(repo, "add", "value.txt")
    git(repo, "commit", "-qm", "add value")
    prompt = tmp_path / "integration-task.md"
    prompt.write_text(
        "Change only value.txt so its exact contents are `after\\n`. Do not create or modify any other file.",
        encoding="utf-8",
    )
    completed = run(
        "--prompt-file", str(prompt), "--workspace", str(repo), "--timeout", "300",
        "--output-format", "json", "--mode", "edit", "--sandbox", "disabled", "--executable", real_agent,
        timeout=360,
    )
    result = json.loads(completed.stdout)
    assert completed.returncode == 0, json.dumps(result, indent=2)
    assert (repo / "value.txt").read_text(encoding="utf-8") == "after\n"
    assert result["git"]["changed_files"] == ["value.txt"]
    assert subprocess.run(["git", "diff", "--check"], cwd=repo).returncode == 0
