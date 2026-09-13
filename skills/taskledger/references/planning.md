# Taskledger planning contract

Use this reference only when the current Codex task is creating or revising an explicit `$taskledger` plan.

## Required proposal shape

The proposal has exactly five top-level fields:

- `requirements`: stable local refs, observable statements, implementation detail, whether implementation is required, and one or more specification locators with optional excerpts.
- `tasks`: stable local refs, one outcome each, explicit implementation scope, observable acceptance criteria, exact deterministic checks, requirement refs, and dependency refs.
- `execution_policy`: one entry per task with its wave, worker profile, parallel-safety decision, and non-empty prospective write surfaces.
- `assumptions`: only material facts the plan relies on.
- `ambiguities`: unresolved product or implementation decisions. A non-empty list cannot be committed.

Every implementation requirement must be covered by at least one task. Direct/non-implementation requirements must not have tasks. Dependencies must be acyclic and must point to lower waves.

## Task boundaries

Create cohesive units with one verifiable outcome and clear ownership. Include likely shared touchpoints—configuration, registries, generated contracts, central exports, cleanup modules, verification scripts, and application entry points—in implementation scope and write surfaces.

Separate semantic foundations from mechanical followers. Prefer a small complex foundation followed by routine rollout tasks when a shared contract must first be decided. Do not create an implementation task whose primary purpose is to re-audit already integrated work.

For a small cohesive vertical change, one task may use lightweight checkpoints during execution. Checkpoints do not replace task-level submission, independent review, or integration.

## Routine versus complex

Prefer `routine` when all of these are true:

- the plan fixes one implementation approach;
- ownership and shared files are explicit;
- named checks deterministically prove the slice;
- failure is local and reversible; and
- no unresolved product, visual, security, data-integrity, concurrency, or compatibility judgment remains.

Use `complex` when architecture or state ownership must be selected, shared contracts need reconciliation, multiple materially plausible approaches remain, or the work carries security, authorization, schema/data, concurrency, destructive, financial, or weakly testable preservation risk.

Size, file count, and the word “migration” do not by themselves make a task complex. A large mechanical rollout may be routine once its contract, pattern, ownership, and checks are fixed. Responsive visual geometry is complex unless executable measurements or snapshots fully determine it.

## Waves and parallel safety

Classify worker complexity separately from parallel safety. Tasks may share a wave only when their prospective write surfaces and behavioral assumptions are independent.

- If surfaces overlap or one task establishes a contract another consumes, give the shared surface one owner and sequence consumers after it.
- Mark every same-wave task `parallel_safe: true` only when it can run beside every other task in that wave.
- A routine task may need to run alone; a complex task may safely run beside unrelated work.
- Recheck dependencies and write surfaces against the current canonical HEAD before preview.

## Checks and evidence

Put exact executable commands in `required_checks`. Use the narrowest checks that prove each task, including strict lint, generated-contract, schema, or metadata checks in the first task that can introduce those failures. Reserve the complete project suite for final integrated review unless a task boundary itself requires it.

Workers later record their checks, and the controller reruns each declared command at the immutable submitted commit. Worker receipts are claims and never replace independent Reviewer conclusions.

## Final planning check

Before `plan preview`, confirm:

- all five top-level fields are present and contain no invented schema fields;
- every requirement and task ref is unique;
- coverage, dependencies, and wave ordering are complete;
- every task has one routing decision and non-empty write surfaces;
- same-wave overlaps have been removed or sequenced;
- acceptance criteria describe observable outcomes;
- checks are exact commands safe to run in an assignment worktree; and
- ambiguities are empty.
