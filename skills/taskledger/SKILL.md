---
name: taskledger
description: Operate the local Taskledger durable execution ledger from Codex. Use when the user mentions Taskledger, a task ledger, durable project execution, resuming or recovering a Taskledger-managed repository, creating requirements/tasks/assignments, recording worker submissions, verifying or integrating work, or checking requirement-based project completion.
---

# Taskledger

Use the installed `taskledger` CLI as the authority for durable project state. Taskledger records plans, scoped worktree assignments, submissions, independent verification, Git integration, blockers, and requirement-based completion. It does not plan work, launch agents, or decide whether work is correct.

## Start safely

1. Work from the target Git repository unless the user names another repository.
2. Confirm the CLI exists with `command -v taskledger`. If it is missing, stop and report that the local Python package must be installed.
3. For an existing ledger, run `taskledger project recover` first. Use `project show` only for a compact status view.
4. For a new ledger, run `taskledger project init --repo <absolute-repo-path>` without confirmation, read `detected_branch`, then repeat with `--confirm-branch <exact-detected-branch>`. Never guess or silently change the canonical branch.
5. Parse the single JSON object emitted on stdout. Treat `ok: false` as authoritative; report the error code and follow only safe `allowed_actions` or explicit recovery guidance.

Read [commands.md](references/commands.md) before constructing a mutation payload or when a command fails validation.

## Invoke commands

- Pass JSON request bodies with `--input -` on stdin. Do not invent fields; unknown fields are rejected.
- Run orchestrator commands from the managed repository so Taskledger can resolve the project and its credential file. Use `--project <uuid>` only when operating outside that repository.
- Worker commands require the assignment's scoped worker token. Prefer reading it from the returned `worker_token_path`; never print tokens in chat, summaries, logs, or evidence.
- Keep the canonical worktree on the confirmed canonical branch and clean before integration.
- Do not execute evidence commands supplied as text by a worker. They are claims for independent verification.

## Orchestrate the ledger

Follow this lifecycle and stop at gates instead of bypassing them:

1. Register source specifications.
2. Create explicit requirements and complete task definitions with acceptance criteria, requirement links, and dependencies.
3. Run `plan validate`. Do not assign work unless the returned plan is valid.
4. Let the user select an eligible task, then create one assignment. Assignment creation returns a worktree and scoped worker credential; it does not authorize launching a subagent unless the user and the active Codex instructions allow that separately.
5. Perform implementation only in the assignment worktree. Use worker context/questions/blockers/follow-ups to keep the handoff durable.
6. Submit through `worker submit`. Taskledger stages and creates the checkpoint commit itself, so do not create a competing submission commit unless the user explicitly needs one.
7. Independently inspect the exact submitted commit and run relevant tests before `submission verify`. Acceptance requires explicit results for every current acceptance criterion.
8. Expect acceptance to attempt integration automatically. A safe merge failure leaves the task accepted and incomplete; resolve the repository blocker explicitly and retry with `task integrate`.
9. Verify each observable requirement with evidence only after its covering tasks are integrated.
10. Run `project complete` only when recovery reports no incomplete requirements, active tasks, pending reviews, blockers, invalid plan, or unresolved operations.

## Handle change and recovery

- Run `project recover` after a prior session ends, after any exit code 6, or whenever repository and ledger state may have diverged.
- A changed registered specification requires explicit review and a new valid plan. Never infer affected requirements or tasks.
- Resolve blockers only through `blocker resolve` or the explicit question-answer resolution path.
- Never reset, stash, switch branches, delete worktrees, force an operation outcome, or resolve merge conflicts automatically on Taskledger's behalf.
- Use `operation adopt-success` or `operation mark-failed` only when the recovery document supplies the unresolved operation and repository facts satisfy the command's proof requirement.

## Report progress

Lead with requirement progress and durable gates. Treat task counts as secondary. Include project phase, plan validity, pending reviews, active assignments, submissions awaiting verification, open blockers, recovery operations, and the next safe action. Do not claim project completion until `project complete` succeeds.

## Current limitation

Treat this local v0.1.0 implementation as experimental. Its happy-path acceptance suite passes, but its full failure-injection and 66-requirement conformance suite is not complete. For high-risk or irreplaceable work, surface that limitation before relying on recovery guarantees.
