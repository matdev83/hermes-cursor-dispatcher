---
name: cursor-delegate
description: "Use when delegating repository-level coding, debugging, refactoring, or implementation work to the installed Cursor CLI (`agent`). Safely transfers exact UTF-8 prompts through a Python wrapper, prefers isolated Git worktrees, captures a structured result, and requires Hermes to inspect diffs and run validation independently before acceptance."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    tags: [cursor, delegation, coding-agent, worktree, security, validation]
    related_skills: [test-driven-development, requesting-code-review, systematic-debugging]
---

# Cursor CLI Delegation

## Overview

Use Cursor CLI as an external implementation executor, never as a source of truth. Hermes owns task construction, workspace selection, safe invocation, diff review, independent tests, corrective iterations, and the final acceptance decision.

The wrapper is `cursor_delegate.py` in this skill directory. It reads an exact UTF-8 prompt from a file and sends it to Cursor through stdin without shell interpretation. Stdin transport was verified against Cursor CLI `agent`; this avoids Linux's per-argument size limit while preserving multiline prompts and shell metacharacters. The wrapper invokes `agent` with `shell=False`, explicit `cwd`, a narrowly allowlisted environment, a timeout, and an isolated process group. Dedicated drain threads retain bounded tails and bounded mode-0600 log prefixes; exceeding either output limit terminates the Cursor process group.

Never invoke Cursor by interpolating arbitrary prompt text into a shell command. Never use `eval`, `shell=True`, an environment variable as prompt transport, `echo`, or an unquoted heredoc.

## When to Use

Use for:

- non-trivial implementation, debugging, migration, or refactoring tasks;
- repository changes where a second coding executor is useful;
- tasks with large Markdown prompts, code blocks, structured data, or shell syntax;
- independent implementation attempts in isolated Git worktrees.

Do not use for:

- a one-line local edit Hermes can safely make and verify directly;
- operations outside a clearly selected workspace;
- prompts containing secret values;
- arbitrary MCP access or implicit privilege escalation;
- accepting Cursor's report without inspecting its actual changes.

## Installed CLI Contract

Before relying on the wrapper in a changed environment, run:

```bash
agent --help
agent --version
```

The version verified when this skill was authored supports:

- executable: `agent`;
- headless mode: `--print`;
- output formats: `text`, `json`, `stream-json`;
- read-only modes: `--mode plan` and `--mode ask`;
- edit mode: the default (there is no `--mode edit` Cursor flag);
- workspace selection: `--workspace <path>`;
- headless trust: `--trust`;
- sandbox selection: `--sandbox enabled|disabled`;
- model selection: `--model <id>`;
- prompt input through stdin.

The wrapper defaults to `grok-4.5-xhigh`. Although this alias was not printed by `agent --list-models`, it was verified directly against the installed CLI with a successful non-interactive execution. Override deliberately with `--model` only after confirming another identifier locally.

The wrapper accepts `--mode edit`, but deliberately omits `--mode` from Cursor's argv for edit mode. It does not pass `--force`, `--yolo`, `--approve-mcps`, `--add-dir`, or arbitrary headers.

If `agent --help` changes, update the wrapper and tests before delegating real work. Do not infer unsupported flags from online examples.

## Mandatory Workflow

### 1. Inspect before delegating

Hermes must first inspect:

- repository structure and applicable instruction files;
- relevant modules, interfaces, and implementation constraints;
- current tests and repository-specific validation commands;
- current branch, `git status --short`, repository root, and `HEAD`;
- whether the current worktree contains unrelated changes.

Do not delegate a vague issue title. Completion criterion: Hermes can name the relevant files, current behavior, required behavior, exclusions, and validation commands.

### 2. Select an isolated workspace

Prefer the wrapper's worktree mode for non-trivial edits:

```bash
python3 ~/.hermes/skills/cursor-delegate/cursor_delegate.py \
  --prompt-file /tmp/hermes-cursor-task.md \
  --workspace /path/to/repository \
  --isolate-worktree \
  --worktree-root /tmp \
  --task-id cursor-example-8f31c2 \
  --timeout 3600 \
  --mode edit \
  --output-format json
```

Use a collision-resistant task ID made from a timestamp and random suffix. Never derive paths or branch names from prompt text. The resulting branch is `hermes/<task-id>` and the worktree path is returned in the JSON envelope.

Skip isolation only when the task is read-only, the source is not Git, worktrees are unavailable, or the user explicitly requires the current tree. This wrapper requires an accessible Git repository; use a separately reviewed execution path for non-Git workspaces.

A dirty source workspace is rejected by default. Use `--allow-dirty-worktree` only after accounting for every existing change. Prefer isolation instead. When `--allowed-root` is supplied, the resolved Git repository root and any isolated worktree destination must also be inside it; choose a root broad enough to contain both reviewed locations.

Completion criterion: the JSON result identifies an explicit workspace and starting commit, and unrelated user work cannot be overwritten.

### 3. Build a self-contained structured prompt

Start from `templates/implementation-prompt.md`. Include:

- precise task and current behavior;
- relevant files, modules, interfaces, and conventions;
- concrete functional requirements and non-requirements;
- compatibility, security, architecture, and scope constraints;
- a TDD procedure and exact validation commands;
- expected deliverables and completion-report format.

Tell Cursor that Hermes will independently inspect and validate all changes. Do not include credentials, tokens, private keys, passwords, or connection strings. Before saving, scan the prompt for obvious secrets and replace values with secret names or configuration references.

Completion criterion: Cursor can implement the task without guessing any essential requirement or reading an external issue title.

### 4. Save the prompt as UTF-8

Use the file tool or Python's `Path.write_text`:

```python
from pathlib import Path
Path("/tmp/hermes-cursor-task.md").write_text(prompt, encoding="utf-8")
```

If a shell heredoc is unavoidable, single-quote its delimiter. Never use an unquoted delimiter for arbitrary prompt text.

The wrapper rejects missing, non-regular, invalid UTF-8, empty, whitespace-only, and oversized prompt files. Default maximum size is 1 MiB; override deliberately with `--max-prompt-bytes`, never by truncating.

Completion criterion: the prompt file's byte length and SHA-256 in the result match the intended file.

### 5. Invoke the wrapper with explicit arguments

Normal edit:

```bash
python3 ~/.hermes/skills/cursor-delegate/cursor_delegate.py \
  --prompt-file /tmp/hermes-cursor-task.md \
  --workspace /tmp/hermes-cursor-example-8f31c2 \
  --timeout 3600 \
  --mode edit \
  --output-format json
```

Read-only analysis defaults:

```bash
python3 ~/.hermes/skills/cursor-delegate/cursor_delegate.py \
  --prompt-file /tmp/hermes-cursor-analysis.md \
  --workspace /path/to/repository \
  --timeout 900 \
  --mode plan \
  --output-format json
```

Use up to 7200 seconds only for intentionally large repository tasks. Keep sandbox enabled unless a known repository or host limitation makes it impossible and the user accepts the increased risk. If sandboxing is unavailable, pass `--sandbox disabled` deliberately and compensate with worktree isolation plus Cursor's allowlist mode.

The wrapper always emits exactly one JSON object on stdout and returns a non-zero wrapper exit status when `ok` is false. It redacts prompt contents from `command`, but records prompt SHA-256 and byte length.

Completion criterion: parse stdout as JSON; do not infer success from process output text.

### 6. Classify the result

Recognized failures:

- `configuration_error` — fix caller arguments; do not retry blindly;
- `prompt_validation_error` — fix the prompt file;
- `workspace_validation_error` — fix workspace selection or dirty state;
- `executable_not_found` — install or locate `agent`;
- `cursor_exit_error` — inspect stdout, stderr, and changes;
- `timeout` — inspect retained changes before retrying;
- `invalid_cursor_json` — retain raw stdout and inspect workspace;
- `output_limit_exceeded` — inspect full log paths and workspace;
- `git_inspection_error` — restore Git accessibility;
- `unexpected_internal_error` — diagnose the wrapper before retrying.

Do not automatically retry configuration or validation failures. Treat `changes_may_exist` as a requirement to inspect before any cleanup or rerun.

### 7. Independently inspect every change

Run inside the returned `workspace`, not the source repository:

```bash
git status --short
git diff --stat
git diff --check
git diff
```

Also inspect staged changes when present:

```bash
git diff --cached --stat
git diff --cached --check
git diff --cached
```

Account for every modified, added, deleted, renamed, generated, and untracked file. Check for:

- unrelated refactors or generated artifacts;
- deleted or weakened tests and validation;
- commented-out code or hardcoded bypasses;
- secrets, credentials, or connection strings;
- debug output and temporary files;
- suspicious config, CI, permission, or dependency changes;
- writes outside the selected worktree.

Cursor's summary and test claims are evidence only, never acceptance proof.

Completion criterion: every changed path is expected and every diff hunk supports a stated requirement.

### 8. Run validation independently

Hermes must execute repository-specific commands itself. Use the commands discovered before delegation, not a generic list. Capture command, exit code, stdout, stderr, and duration.

Examples only:

```bash
pytest -q
go test ./...
go vet ./...
npm test
npm run lint
```

At minimum run:

- focused tests for changed behavior;
- the relevant package or project test suite;
- `git diff --check`;
- repository-required lint, type, build, or static-analysis commands.

Completion criterion: required checks pass in Hermes's own tool output, independent of Cursor's report.

### 9. Correct in a bounded loop

On validation failure, write a new follow-up prompt containing:

- concise original task summary;
- exact failed command and exit status;
- relevant error output;
- current diff context;
- constraints against regressions and unrelated edits;
- requirement to inspect existing changes before editing.

Invoke Cursor again in the same retained worktree. Use at most three Cursor invocations per delegated task. Do not hide retries inside the wrapper. After the limit, stop and report the failure with retained workspace path.

Completion criterion: either independent validation passes or Hermes returns a grounded failure report after no more than three invocations.

### 10. Accept, transfer, or retain

Accept only when:

- wrapper execution succeeded or Hermes explicitly accepted a reviewed partial result;
- prompt integrity metadata is consistent;
- all changed files and hunks are expected;
- no quoting or shell-injection path exists;
- independent tests and required static checks passed;
- no secrets, debug artifacts, or unrelated changes remain;
- dependency changes are justified;
- risks and assumptions are documented.

Do not automatically destroy a worktree containing unreviewed changes. After accepted changes have been committed/transferred, or after Hermes explicitly decides to discard them, use the cleanup commands returned in `worktree.cleanup_commands`:

```bash
git worktree remove /tmp/hermes-<task-id>
git branch -D hermes/<task-id>
```

Completion criterion: accepted work is transferred safely, or rejected work remains at a clearly reported path until explicitly discarded.

## Result Envelope

Important fields include:

```json
{
  "ok": true,
  "exit_code": 0,
  "timed_out": false,
  "workspace": "/tmp/hermes-cursor-...",
  "starting_commit": "abc123...",
  "command": ["/path/to/agent", "--print", "...", "<prompt via stdin omitted>"],
  "prompt_sha256": "...",
  "prompt_bytes": 18432,
  "cursor_version": "<detected-version>",
  "stdout": "...",
  "stderr": "",
  "parsed_output": {},
  "git": {
    "starting_status_porcelain": "",
    "status_porcelain": "?? changed.py",
    "changed_files": ["changed.py"],
    "diff_stat": "...",
    "diff_check_exit_code": 0,
    "changes_may_exist": true
  }
}
```

When output exceeds a configured limit, the wrapper terminates Cursor, retains only the bounded UTF-8 tail in the envelope, and gives a mode-0600 temporary path containing a bounded output prefix. It never attempts to preserve unlimited output. Inspect the retained prefix, tail, and workspace before retrying.

On POSIX, Cursor runs in a new session/process group. Timeout and output-limit paths send `SIGTERM`, wait briefly, then send `SIGKILL` if needed. On Linux, the wrapper also snapshots `/proc` descendants before termination and signals children that escaped the original group with `setsid()`. After any direct Cursor exit, it terminates remaining same-group descendants, including children that detached their standard streams. Prompt writes and output drains use nonblocking descriptor loops with explicit stop events, so retained descriptors cannot hold the wrapper open indefinitely. Inspect the retained workspace after any abnormal result because changes may exist.

## Security Boundaries

- No shell interpretation: prompt transport uses stdin and `shell=False`.
- No privilege escalation: never add `sudo`.
- One workspace: do not pass `--add-dir` unless the user explicitly expands scope.
- Sandbox on: default is `--sandbox enabled`.
- No force approval: do not add `--force` or `--yolo` casually.
- No arbitrary MCP approval: never add `--approve-mcps`; approve only known servers through an explicit allowlist and separate review.
- Controlled environment: the wrapper forwards only an explicit variable allowlist. It does not forward wildcard `GIT_*`, `NPM_*`, `NODE_*`, `PYTHON*`, or `CURSOR_*` variables; only the specifically supported `CURSOR_API_KEY` credential is eligible. Environment contents are never logged.
- No secrets in prompts: reference configuration names, not values.
- Existing network policy applies; do not assume unrestricted network access.

## Tests and Maintenance

Unit tests use a fake `agent`; they must never consume Cursor service usage:

```bash
cd ~/.hermes/skills/cursor-delegate
python3 -m venv /tmp/cursor-delegate-test-venv
/tmp/cursor-delegate-test-venv/bin/pip install pytest ruff mypy
/tmp/cursor-delegate-test-venv/bin/pytest -q tests/test_cursor_delegate.py -m 'not cursor_integration'
```

The real integration test is opt-in and may consume authenticated Cursor usage:

```bash
RUN_CURSOR_INTEGRATION_TESTS=1 \
  /tmp/cursor-delegate-test-venv/bin/pytest -q -m cursor_integration
```

Run it only when authentication, network policy, and usage cost are intentionally configured.

## Common Pitfalls

1. Passing the prompt as a shell fragment. Save UTF-8 and use the wrapper.
2. Passing a very large prompt as one argv element. Linux commonly rejects single arguments near 128 KiB; this wrapper uses verified stdin transport.
3. Treating `--mode edit` as a Cursor flag. It is wrapper vocabulary; Cursor edit mode is the absence of a read-only mode flag.
4. Delegating into a dirty user worktree. Prefer `--isolate-worktree`; do not normalize danger with `--allow-dirty-worktree`.
5. Believing a zero Cursor exit proves correctness. Review all diffs and independently run tests.
6. Cleaning up after failure. Retain any workspace where changes may exist until reviewed.
7. Retrying malformed configuration. Correct it first; only implementation/test failures belong in the bounded corrective loop.
8. Logging the full prompt. Use SHA-256 and byte length for identity; inspect the original restricted file only when necessary.

## Verification Checklist

- [ ] `agent --help` and `agent --version` remain compatible
- [ ] source Git status and repository constraints inspected
- [ ] isolated worktree used for non-trivial edits
- [ ] prompt is self-contained, UTF-8, and secret-free
- [ ] wrapper JSON parsed and prompt hash/length recorded
- [ ] all changed paths and hunks independently reviewed
- [ ] focused and repository-required validations run by Hermes
- [ ] no unrelated changes, secrets, debug artifacts, or weakened tests
- [ ] no more than three Cursor invocations
- [ ] retained worktree path reported until accepted or discarded
