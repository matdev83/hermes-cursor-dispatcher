# Hermes Cursor Dispatcher

Hermes Agent skill for safely delegating repository work to Cursor CLI (`agent`). Includes exact prompt transport, worktree isolation, bounded output, process cleanup, and structured JSON results.

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
