# Complexity-adjusted Taskledger comparison

## Why task count is insufficient

An integrated task can be a small sibling endpoint, a cross-cutting architecture change, or a final verification gate. Days versus minutes is not itself the error: total tokens divided by completed tasks removes wall-clock duration. The remaining and more important error is treating unlike tasks as equal units of work.

No single observable is an adequate complexity denominator. Lines changed reward verbosity, elapsed time includes idle periods, and acceptance criteria differ in breadth. Keep the dimensions visible and use matched task families before aggregate ratios.

## Predeclared comparison hierarchy

Use the highest available tier and report lower tiers only as sensitivity checks:

1. **Repeated sibling match:** same specification, layer, operation pattern, scope boundary, acceptance-criterion band, and similar changed-line/file band. This is the primary evidence.
2. **Task-family match:** same layer and task type, with results stratified by criteria, changed files/lines, submissions, and corrections.
3. **Portfolio standardization:** compare exact model/effort cohorts across a collection with a similar mix of task families. Report every dimension and use medians plus totals.
4. **Durable-event normalization:** tokens per integration, verification, or submission. Use only as a workflow-level context signal.

Never use a server-architecture portfolio as the primary comparator for a narrow endpoint portfolio. Never convert lower-tier model tokens into hypothetical higher-tier tokens.

## Measurement units

For planning, record separately:

- exact model and reasoning effort;
- specification size and revision count;
- requirements, source references, tasks, dependencies, and total acceptance criteria created;
- specification reviews, plan validations, approval cycles, compactions, and input/output tokens.

For execution, record per task:

- assignment activation and successful integration timestamps;
- exact-model input, cached input, output, token events, and compactions inside that window;
- criteria, linked requirements, dependencies, assignment attempts, submissions, rejected/blocked reviews;
- Git files, additions, deletions, and top-level areas changed.

Planning tokens must not be silently allocated across execution tasks. Idle time does not inflate token totals, but version-spanning tasks and overlapping task windows must be flagged.

## Next-run decision rule

Before viewing the next run's token result, classify its tasks by family. Compare only families with a defensible prior match. A reduction is persuasive when:

- exact model/effort cohorts fall at least 20% within matched families;
- the result holds under both per-criterion and per-changed-line sensitivity checks;
- rejection, correction, blocker, recovery, and compaction rates do not worsen;
- at least three comparable tasks support the family estimate.

With no comparable pre-change family, establish a v0.3.0 baseline rather than claiming improvement. After another configuration change, repeat the same family to create a clean A/B. Prefer a median task ratio and show each task; do not let one architecture task dominate the conclusion.

## Current financial-tracker classification

The broad pre-v0.3.0 checkpoint is historical context only. The first post-change queue was already planned. Its insurance task spans both configurations and is censored. Activity, transportation, and hotel-stay are the cleanest stable v0.3.0 sibling family. The checkpoint at `2026-08-22T19:28:55Z` begins the first brand-new planning-and-execution sample.
