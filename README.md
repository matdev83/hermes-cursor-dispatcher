# Hermes Cursor Dispatcher

Hermes Agent skill and Python wrapper for safely delegating repository coding tasks to Cursor CLI (`agent`).

It provides exact UTF-8 prompt transport, Git/worktree checks, bounded output capture, process cleanup, structured JSON results, and independent-validation instructions for Hermes.

## Install

Requirements: Python 3.10+, Git, Hermes Agent, and an authenticated Cursor CLI.

```bash
git clone https://github.com/matdev83/hermes-cursor-dispatcher.git ~/.hermes/skills/cursor-delegate
```

Start a new Hermes session so the skill is discovered.

## Use

```bash
python3 ~/.hermes/skills/cursor-delegate/cursor_delegate.py \
  --prompt-file /tmp/task.md \
  --workspace /path/to/repo \
  --timeout 3600 \
  --mode edit \
  --output-format json
```

The default Cursor model is `grok-4.5-xhigh`; override it with `--model`.

## Test

```bash
python3 -m venv .venv
.venv/bin/pip install pytest ruff mypy
.venv/bin/pytest -q -m 'not cursor_integration'
```

Real Cursor tests are opt-in:

```bash
RUN_CURSOR_INTEGRATION_TESTS=1 .venv/bin/pytest -q -m cursor_integration
```

MIT licensed.
