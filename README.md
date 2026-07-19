# Hermes Cursor Dispatcher

Hermes Agent skill for safely delegating repository work to Cursor CLI (`agent`). Includes exact prompt transport, worktree isolation, bounded output, process cleanup, and structured JSON results.

> 🚀 **Tease the Future:** Run the newest **Grok models** (including `grok-4.5-xhigh`) directly via Cursor CLI integrations, keeping your coding agent state-of-the-art!

## Key Features

* **Latest LLM Capabilities**: Easily delegate implementation tasks leveraging the latest Grok models through Cursor CLI command-line execution.
* **Isolated Worktrees**: Creates an isolated scratch Git worktree automatically (`git worktree add`) so Cursor never corrupts your main working branch or dirty files.
* **Secure Stdin Prompt Transport**: Transports prompts via standard input to prevent shell interpretation exploits and avoid command line argument size limitations.
* **Orphan Cleanup & Execution Bounds**: Monitors output buffers and enforces strict process group timeouts to clean up runaway sub-executors immediately.
* **Independent Verification Loop**: Hermes validates Cursor's outputs through independent tests and diff reviews before accepting any modification.

## Install

Requires Python 3.10+, Git, Hermes Agent, and an authenticated Cursor CLI.

```bash
hermes skills tap add matdev83/hermes-cursor-dispatcher
hermes skills install matdev83/hermes-cursor-dispatcher/cursor-delegate --force --yes
```

Or install directly:

```bash
hermes skills install https://raw.githubusercontent.com/matdev83/hermes-cursor-dispatcher/main/skills/cursor-delegate/SKILL.md --force --yes
```

`--force` is required because the community-skill scanner flags the wrapper's intentional subprocess execution. Inspect the skill before installing if desired.

Start a new Hermes session, then load `cursor-delegate` when delegating coding work.

## Bundle

```text
skills/cursor-delegate/
├── SKILL.md
├── scripts/cursor_delegate.py
└── templates/implementation-prompt.md
```

Hermes installs the referenced Python wrapper and prompt template with the skill. The skill runs the wrapper with `python3 ${HERMES_SKILL_DIR}/scripts/cursor_delegate.py`; no separate Python package installation is required.

Repository-level tests are retained for contributors but are not installed with the skill.

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
