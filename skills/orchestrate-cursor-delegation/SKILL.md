---
name: orchestrate-cursor-delegation
description: "Use when executing an approved software implementation plan by orchestrating one or more Cursor CLI workers through the cursor-delegate skill. Decompose work into self-contained tasks, provide complete fresh-context prompts, enforce TDD, inspect every retained worktree and diff, run independent validation, and issue bounded repair delegations until the approved plan is complete or a hard failure is reached."
version: 1.0.0
author: matdev83
license: private
platforms: [linux, macos]
metadata:
  hermes:
    tags: [orchestration, cursor, delegation, implementation, tdd, code-review]
    related_skills: [cursor-delegate, test-driven-development, requesting-code-review]
    requires_toolsets: [terminal, file]
---

# Orchestrate Cursor Delegation

## Overview

Act as the implementation orchestrator for the current approved plan. Delegate suitable repository work to Cursor through `cursor-delegate`, but keep ownership of decomposition, context construction, sequencing, review, integration, validation, retries, and final acceptance.

Cursor is an implementation worker, not a source of truth. Each invocation starts without this conversation's history, user messages, plan discussion, or Hermes findings. Every Cursor prompt must therefore be self-contained. Never assume that a previous invocation's explanation is available to a later invocation; restate all context that matters.

This skill replaces harness-specific “general subagent” execution. Do not substitute Hermes `delegate_task` workers when this skill governs an approved coding plan. Load and follow `cursor-delegate` for every Cursor invocation.

## When to Use

Use when:

- the user has approved a multi-step implementation plan;
- implementation can be divided into one or more reviewable repository tasks;
- Cursor is intended to perform some or all production-code changes;
- Hermes can independently inspect and validate the resulting work.

Do not use when:

- no implementation plan exists or material architectural choices remain unapproved;
- the task is a trivial local edit that Hermes can safely make and verify directly;
- the selected workspace, repository guidance, or validation commands are unknown;
- required prompt context would expose credentials or other secrets.

If the plan is absent or materially ambiguous, stop execution and obtain an approved plan before delegating.

## 1. Load the Executor Contract

Load `cursor-delegate` with `skill_view` and follow its current workflow, safety boundaries, wrapper contract, worktree policy, output handling, and cleanup rules. Treat that skill as the source of truth for invoking Cursor; do not duplicate or guess wrapper flags from memory.

Completion criterion: the `cursor-delegate` skill is available and Hermes knows the installed wrapper path, workspace policy, result-envelope fields, and host-specific sandbox constraints.

## 2. Establish the Execution Baseline

Before decomposition, inspect:

- the approved plan and all additional user instructions;
- repository guidance such as `.hermes.md`, `AGENTS.md`, or equivalent project rules;
- repository root, branch, `HEAD`, worktree status, staged and untracked files;
- relevant modules, interfaces, tests, architecture, naming, and style;
- exact focused and project-level validation commands;
- dependencies among plan steps and any migration or compatibility constraints.

Preserve the approved intent. Do not silently change scope, architecture, public APIs, data formats, compatibility guarantees, or repository conventions. If implementation evidence invalidates the plan, pause and ask for approval rather than quietly redesigning it.

Completion criterion: Hermes can state the initial repository condition, required behavior, non-requirements, task dependencies, and validation gates without asking Cursor to guess.

## 3. Build a Task Graph

Decompose the plan into the smallest independently reviewable tasks that still produce coherent behavior. For each task define:

1. **Scope** — precise files, modules, behavior, and boundaries.
2. **Context** — current behavior, relevant interfaces, prior accepted changes, and repository conventions.
3. **Requirements** — observable functional outcomes.
4. **Design rules** — approved architecture, naming, public API, compatibility, and dependency constraints.
5. **TDD sequence** — tests or interface changes first, observable RED state, then minimal implementation and GREEN state.
6. **Deliverables** — code, tests, documentation, migrations, or generated artifacts that are actually required.
7. **Acceptance criteria** — a checkable definition of done, including exact commands.
8. **Exclusions** — unrelated cleanup, speculative abstraction, dependency churn, broad formatting, or out-of-scope refactors.

Sequence tasks by dependency. Use sequential delegation by default. Parallel Cursor work is allowed only when tasks are genuinely independent, each uses a separate isolated worktree, and Hermes defines an explicit transfer and integration order before starting.

Completion criterion: every plan requirement maps to at least one task and acceptance criterion, while no task expands the approved scope.

## 4. Construct a Fresh-Context Cursor Prompt

For every invocation, create a complete prompt using the template bundled with `cursor-delegate`. Include all information the worker needs:

- absolute repository path and selected execution workspace;
- concise approved-plan context and the current task's place in it;
- current relevant implementation behavior and files;
- exact requirements, exclusions, and design constraints;
- repository guidance and conventions that affect the task;
- interfaces or changes produced by previously accepted tasks;
- exact TDD procedure and validation commands;
- required deliverables and definition of done;
- instruction not to commit unless explicitly requested;
- instruction to report changed files, tests run, failures, assumptions, and residual risks;
- notice that Hermes will inspect every diff and rerun validation independently.

Treat the user's current request and any text following the skill invocation as additional human instructions. This is the Hermes equivalent of another harness's `$ARGUMENTS`; incorporate it explicitly into task scope and constraints instead of preserving a literal placeholder.

Never include secrets or rely on references such as “as discussed above,” “follow the current plan,” or “use our earlier decision.” Cursor has none of that context.

Completion criterion: a fresh worker could implement the task correctly using only the repository and saved prompt.

## 5. Delegate Through `cursor-delegate`

Use the installed Cursor wrapper and prefer an isolated Git worktree for every non-trivial edit. Use a unique task ID and retain the returned workspace until Hermes has reviewed and accepted or explicitly discarded it.

Do not invoke Cursor by interpolating prompt text into a shell command. Do not enable force/yolo behavior, arbitrary MCP approval, extra directories, privilege escalation, or sandbox disabling except as permitted by `cursor-delegate` for a verified host limitation with compensating isolation and allowlists.

Parse the wrapper's one-object JSON result. Record at least:

- `ok`, exit status, timeout state, and error classification;
- returned workspace and starting commit;
- prompt byte length and SHA-256;
- changed-file inventory and whether changes may exist;
- retained output or log paths relevant to diagnosis.

A successful Cursor exit is only the start of review, not completion.

Completion criterion: Hermes has a structured result tied to a known prompt, starting commit, and retained workspace.

## 6. Review Every Worker Result

Review inside the returned workspace. Inspect:

- `git status --short`;
- unstaged and staged diffs, including stats and `git diff --check`;
- every modified, added, deleted, renamed, generated, and untracked file;
- correctness against the task and approved plan;
- integration with previously accepted work;
- test quality and evidence of the requested RED-GREEN sequence;
- failure paths, edge cases, regression coverage, and robust error handling;
- maintainability, simplicity, repository consistency, and avoidable shortcuts;
- unrelated refactors, dependency changes, weakened tests, debug artifacts, hardcoded bypasses, or secrets.

Do not accept Cursor's summary or claimed test results without matching filesystem evidence. Reject over-engineered, brittle, under-tested, incomplete, or convention-breaking work even when the command exited successfully.

Completion criterion: every changed path and hunk is accounted for, and Hermes can explain why it satisfies or violates the task's acceptance criteria.

## 7. Validate Independently

Run the exact repository-specific commands yourself in the returned workspace. At minimum include:

- focused tests for the changed behavior;
- relevant package or project tests;
- `git diff --check`;
- required lint, type-check, build, migration, or static-analysis commands.

Verify that tests genuinely exercise new behavior, edge cases, regressions, and failure paths. Do not count a test merely because it exists or passes. Record what passed, what failed, and what was not run.

Completion criterion: acceptance checks pass in Hermes tool output, independent of Cursor's report.

## 8. Repair in a Bounded Loop

If review or validation finds a defect, delegate a targeted repair in the same retained worktree. The repair prompt must restate:

- the task and relevant approved-plan context;
- current changed-file and diff state;
- exact failing command, exit status, and relevant output;
- the specific defect or missing acceptance criterion;
- constraints against regressions, unrelated edits, and wholesale rewrites;
- required tests and final validation.

Before retrying after timeout, empty output, wrapper error, or another technical failure, inspect the retained workspace because partial changes may exist. Continue from reviewed progress rather than blindly starting over.

Allow at most three Cursor invocations for a given task, including repair attempts. Three consecutive technical failures for that task—timeout, empty unusable output, or execution error—are a hard failure: stop, preserve the workspace, and report the evidence. Do not disguise extra retries as new tasks.

Completion criterion: the task passes review and validation within three invocations, or execution stops with a grounded hard-failure report and retained recovery path.

## 9. Integrate and Recheck the Plan

After accepting a task, transfer or commit it using the repository's established workflow. Then update downstream Cursor prompts with the exact accepted interfaces and current commit; do not expect later workers to infer earlier changes.

After all tasks:

- map the final diff back to every approved-plan requirement;
- run integration tests and the broadest required project validation;
- inspect the aggregate diff and final Git status;
- verify documentation, migrations, generated files, and compatibility work where required;
- confirm no unrelated changes or temporary artifacts remain.

Do not clean a worktree containing unreviewed or untransferred changes. Use cleanup commands only after explicit acceptance and safe transfer, or after deciding to discard disposable work.

Completion criterion: the integrated repository satisfies every approved requirement and all required independent checks pass, or a clearly identified blocker remains.

## Final Report

Report:

- tasks delegated and Cursor invocation count per task;
- accepted implementation and key changed files;
- independent test, lint, type, build, and migration results;
- checks not run and why;
- deviations from the approved plan, which require explicit approval;
- retained worktree paths for incomplete or failed work;
- residual risks, assumptions, and recommended follow-up.

Never claim completion from worker assertions alone.

## Common Pitfalls

1. **Thin prompts:** referring to session history instead of restating context. Fix by making every prompt independently executable.
2. **Vague delegation:** sending a plan heading without scope or definition of done. Fix by writing a complete task packet.
3. **Production-first implementation:** asking for code before tests. Require interface/test changes and a RED observation before implementation, unless the repository makes that impossible and the exception is explained.
4. **Blind acceptance:** trusting Cursor's success message. Inspect every diff and rerun validation.
5. **Retry reset:** discarding useful partial work after a technical failure. Inspect and continue in the retained workspace.
6. **Retry laundering:** exceeding three attempts by renaming the same failed task. Stop at the hard limit.
7. **Premature cleanup:** deleting a failed worktree before review or recovery. Retain and report it.
8. **Silent redesign:** changing approved architecture in response to implementation friction. Escalate the decision to the user.
9. **Parallel conflicts:** delegating overlapping tasks concurrently. Default to dependency-ordered sequential execution.

## Verification Checklist

- [ ] Approved plan and additional human instructions preserved
- [ ] `cursor-delegate` loaded and followed for every worker invocation
- [ ] Repository baseline, guidance, relevant code, and validation commands inspected
- [ ] Every task has complete context, scope, constraints, deliverables, and definition of done
- [ ] TDD and strong edge/failure/regression coverage required
- [ ] Separate isolated worktree used for each non-trivial task
- [ ] Every worker result and every changed path independently reviewed
- [ ] Relevant validation rerun by Hermes, not merely reported by Cursor
- [ ] No task exceeded three Cursor invocations
- [ ] Integrated result mapped back to every approved-plan requirement
- [ ] Unreviewed or failed worktrees retained and reported
- [ ] Final report distinguishes passed, failed, skipped, and residual risk
