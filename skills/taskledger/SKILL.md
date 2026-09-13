---
name: taskledger
description: Prepare, inspect, resume, or troubleshoot Taskledger-managed repository work. Explicit `$taskledger` invocation uses the current Codex task to plan, stops for exact user approval, and commits the approved plan without starting execution.
metadata:
  version: "0.6.0"
---

# Taskledger

Taskledger is the durable authority. Keep CLI JSON internal and explain outcomes in plain language. The foreground Python controller owns execution after the user separately runs `taskledger start` or `taskledger ui`; no model remains alive as the scheduler.

When the user explicitly invokes `$taskledger`, follow the four phases below. Do not reconsider whether Taskledger should be used: explicit invocation already selected this workflow. The current Codex task creates the initial plan; do not launch the Task Creator, workers, Reviewers, assignments, or controller during preparation.

The separate human terminal workflow remains `taskledger bootstrap`, `taskledger prepare --spec <path>`, approval at the prompt, then `taskledger start` or `taskledger ui`. That path uses the bounded Task Creator. Do not substitute it for the explicit-skill workflow.

Read [planning.md](references/planning.md) before creating or revising a plan. Read [commands.md](references/commands.md) before invoking the CLI or after a validation error.

## 1. LOAD

1. Work in the target Git repository. Confirm `taskledger` is installed, read the requested specification, and inspect the repository read-only.
2. Record the exact repository root, symbolic branch, HEAD or unborn state, cleanliness, specification path, relevant implementation surfaces, ignored local inputs, and required services.
3. Run model-safe bootstrap with the exact detected branch:

   `taskledger bootstrap --confirm-branch <exact-branch> --commit-setup`

   This is the only mutation allowed before plan approval. It may install missing profiles, add the local ignore rule, initialize Taskledger, and commit only those generated setup files as `Configure Taskledger`. It must refuse a pre-existing dirty worktree and must never overwrite customized profiles.
4. If setup or storage cannot be safely established, stop and explain the concrete blocker. Never create a second ledger home or continue with chat-only gates.

## 2. PLAN

Create the complete structured proposal in the current Codex task. It must contain requirements, source locators, tasks, implementation scopes, acceptance criteria, exact checks, dependencies, `routine`/`complex` routing, waves, write surfaces, parallel-safety decisions, assumptions, and ambiguities.

Send the candidate to the read-only `taskledger plan preview --input -` command. Use its normalized proposal, deterministic rendering, and `plan_hash` as the approval candidate. `plan preview` must not register specifications, materialize tasks, create preparations, or start controller state.

If the CLI reports an ambiguity, invalid plan, unsafe overlap, missing input, or stale repository fact, revise or stop. Do not proceed to approval with unresolved ambiguities.

## 3. APPROVAL

Show the exact deterministic preview and its `plan_hash`. The final non-blank line of every initial or revised proposal response must be exactly:

`Approve this plan?`

Then end the turn immediately, with no text after that question. Before approval, do not run `plan commit`, register or revise specifications, materialize requirements or tasks, create assignments, launch workers, or start the controller.

A requested change is not approval. Revise the candidate, rerun `plan preview`, show the complete replacement and new hash, end with the same exact approval question, and stop. Never summarize a revision without restoring this approval gate. Accept approval only when it unambiguously refers to the current displayed hash.

## 4. COMMIT

After explicit approval, submit the unchanged preview request to `taskledger plan commit --input -` with the exact `approve_plan_hash`. Do not reconstruct or silently edit the proposal. Taskledger must reject any change to the plan, specification, Git baseline, profiles, preflight inputs, services, or execution configuration.

Verify that the command returns `READY`. It may persist the exact specification and startable preparation, but it must not create assignments, launch agents, or start an execution controller.

End with only this handoff, using the repository's absolute path:

```text
Taskledger is prepared with <task-count> tasks across <wave-count> execution waves.

To begin:

  cd <absolute-path> && taskledger start

Or use the visual interface:

  cd <absolute-path> && taskledger ui
```

Do not start execution, wait for events, or continue scheduling in the preparation task.

## Other Taskledger requests

For inspection, reports, troubleshooting, blocked decisions, or an already-running project, use only the applicable commands in [commands.md](references/commands.md). Do not apply the four-phase preparation workflow unless the user explicitly invokes `$taskledger` or explicitly asks for model-driven guarded preparation.

Treat v0.6.0 as experimental: its happy path is tested, but interrupted-operation conformance is not complete. Surface that limitation before relying on it for high-risk or irreplaceable work.
