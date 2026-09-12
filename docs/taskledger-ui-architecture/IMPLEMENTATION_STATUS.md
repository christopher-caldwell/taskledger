# Task Ledger visual console implementation status

This document records the gap between the architecture specified in
`01-headless-mechanics-spec.md` and `02-textual-display-spec.md` and the current
implementation. The omitted work below is part of the supplied specification;
it is not optional presentation polish.

## Implemented foundation

The current working tree provides:

- immutable, framework-neutral snapshot, history, receipt, status, and error
  values;
- a non-daemon engine thread that owns its SQLite connection and application
  objects;
- a bounded latest-value feed with atomic attachment, stale-revision rejection,
  and cancellation isolation;
- committed-state notifications after successful transactions, with no rollback
  notification and no effect on operation success when observation fails;
- compact projections for project, preparation, run, task, session, usage,
  intervention, and recent activity state;
- controller lifecycle ownership extracted for use by the existing CLI,
  including signal routing, runtime cleanup, and interruption reconciliation;
- an optional Textual 8.2.8 dependency, lazy `taskledger ui` command, and the six
  specified top-level views;
- literal display-text handling, one application-level feed subscription, and
  read-only display of an uninitialized repository.

## Required work still outstanding

### Lifecycle operations

`EngineHost` does not yet execute the three spend-bearing operations:

- `prepare_project`;
- exact proposal approval through `start_prepared_project`;
- `resume_run`.

These methods currently return `UNSUPPORTED_OPERATION`. Their existing CLI
workflows must be moved completely behind the application boundary while
preserving preparation-attempt accounting, live consent, specification revision
and proposal hash checks, repository staleness checks, profile resolution,
project locking, partial-commit behavior, runtime closure, and uncertainty
reconciliation.

This is required by Part 1 sections 6.3–6.5, 9, and 15, and mandatory cases M02,
M12–M18, and M24.

### Admission, ownership, and pause semantics

The host still needs the complete lifecycle admission implementation:

- retain up to 32 unresolved operations without eviction;
- serialize preparation/start/resume admission before the first await;
- acquire the project lease before creating a durable running record;
- bind accepted lifecycle tasks into the host's active-run registry;
- check pause immediately before every provider dispatch and after awaited
  admission work for planning, workers, reviewers, and final review;
- publish transient operation and pending-pause state before long blocking work;
- complete guarded shutdown while a lifecycle is active.

The controller currently checks a stop request at its project-loop boundary, and
CLI interruption uses the extracted reconciliation path. This does not yet cover
every safe point and race required by Part 1 sections 6.4 and 9.

### Query and notification completeness

The compact snapshot is usable, but these required projections remain missing or
incomplete:

- task dependencies, assignment/submission/check status, and evidence references;
- preparation attempt/run-group provenance and full immutable proposal details;
- complete turn history, accounting precision, and allocation completeness;
- configured limits, grants, consumption, and explicit gate status;
- final review state and requirement-verification coverage;
- action eligibility and observation-health metadata;
- explicit pagination beyond the compact 1,000-task view.

Notifications currently invalidate every scope after each transaction. This is
safe for commit ordering and convergence, but domain-specific scope and subject
classification remain to be implemented. Compact history records for turn
acknowledgement, terminal turn outcome, and session closure also remain.

These items are specified in Part 1 sections 7 and 8 and cases M05, M07, M20–M23.

### Textual interaction and layout

The Textual adapter is presently a live display shell. It still needs:

- task master/detail navigation with stable-ID selection and stale-detail guards;
- bounded historical activity loading, source-aware deduplication, follow mode,
  and scroll preservation;
- exact preparation review and fingerprint display;
- forms for prepare, approve/start, resume, answer, blocker resolution, and budget
  extension;
- pending, applied, rejected, failed, and uncertain operation presentation;
- the guarded-exit dialog and fatal-display safe-stop path;
- distinct wide, compact, and below-minimum layouts that preserve state on resize;
- complete focus, mouse, modal, shortcut, and unsent-input behavior.

These behaviors are required by Part 2 sections 3–8 and cases T03–T22 and T24.

### Acceptance and performance evidence

The mandatory M01–M24 and T01–T24 matrices have not yet been fully ported to real
Task Ledger fixtures. Remaining evidence includes observer-equivalence testing,
complete mutation-hook coverage, partial-start failures, admission and pause
races, application-failure shutdown, macOS/Linux signal coverage, UI/headless
workflow equivalence, idle query counts, and install tests with and without the
extra.

The specified fixture of approximately 1,000 tasks, 20 sessions, and 200 events
has not been measured for projection latency, commit-to-visible latency,
blocked-engine responsiveness, or observer overhead. The 26 supplied validation
experiments remain synthetic supporting evidence and do not satisfy the real
implementation acceptance requirements.

## Current verification

The current working tree has passed:

```sh
TASKLEDGER_LIVE_CODEX=0 PYTHONPATH=src python3 -m unittest discover -s tests -q
# Ran 167 tests in 55.879s; OK (skipped=11)

PYTHONPATH=src python3 -m unittest tests.test_console_application -q
# Ran 9 tests; OK

python -m unittest tests.test_console_ui -q
# Ran 2 tests; OK in a temporary environment with taskledger[tui] installed

PYTHONPATH=src python3 -m compileall -q src
git diff --check
```

## Completion criterion

The console is complete only when the three lifecycle operations execute through
the headless host, every UI mutation uses that boundary, and every mandatory
acceptance case passes or has an explicitly accepted limitation. The current
state is a read-only visual-console foundation with short intervention mutations,
not the finished architecture defined by the specification.
