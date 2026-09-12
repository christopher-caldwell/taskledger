# Headless console acceptance evidence

This report covers Part 1 of the visual-console architecture at the current
working tree. The Textual acceptance matrix is intentionally outside this
report.

## Environment and commands

- Platform: macOS 26.6.2, arm64
- Python: CPython 3.13.2
- Full non-live regression:

  ```sh
  TASKLEDGER_LIVE_CODEX=0 PYTHONPATH=src python3 -m unittest discover -s tests -q
  # Ran 185 tests in 67.683s; OK (skipped=11)
  ```

- Focused application and lifecycle regression:

  ```sh
  PYTHONPATH=src python3 -m unittest tests.test_console_application tests.test_initial_planning tests.test_controller tests.test_project_controller tests.test_controller_hardening -q
  # Ran 118 tests in 25.212s; OK (skipped=1)
  ```

- Static verification:

  ```sh
  PYTHONPATH=src python3 -m compileall -q src
  git diff --check
  ```

No live provider execution was used. Linux was not available in this workspace,
so Linux signal and subprocess behavior remains a platform coverage limitation;
the portable code paths and macOS signal behavior are exercised here.

## Mandatory mechanics matrix

| Case | Automated evidence | Result |
|---|---|---|
| M01 | `HostIntegrationTests.test_cli_import_does_not_import_textual`; missing-extra CLI tests in `test_console_ui` | Pass |
| M02 | Full legacy acceptance suite; initial-planning CLI wrapper tests | Pass |
| M03 | `test_controller`, `test_project_controller`, and `test_controller_hardening` fake-runtime suites | Pass |
| M04 | Transaction tests cover absent/failing sinks; fake controller suites run with the default no-op sink and the host refresh sink cannot change scheduler inputs | Pass |
| M05 | Service and Journal committed transactions invalidate observation; controller suites exercise broker, review, integration, and usage paths | Pass |
| M06 | `CommittedNotificationTests` rollback, visibility, and observer-failure cases | Pass |
| M07 | `HostIntegrationTests.test_snapshot_and_paged_queries_are_pure_under_write_denial` | Pass |
| M08 | `FeedTests.test_atomic_seed_and_burst_coalescing` | Pass |
| M09 | The same test publishes 10,000 revisions into one pending slot; history reads durable streams independently | Pass |
| M10 | `FeedTests.test_cancelled_wait_does_not_close_feed`; engine operations use strong task ownership and shielded admission waits | Pass |
| M11 | `HostIntegrationTests.test_engine_host_projects_real_ledger_on_owner_thread` | Pass |
| M12 | Registry-capacity tests plus stale-specification, proposal-hash, active-run, and project-lock tests | Pass |
| M13 | Initial-planning ambiguity, preflight, specification-mutation, diagnostic-failure, and attempt-lineage tests | Pass |
| M14 | Worker/reviewer/planner admission-latch tests, acknowledged-turn interruption, lease reconciliation, and completion race tests | Pass |
| M15 | Controller uncertain-outcome and dispatch-boundary recovery tests | Pass |
| M16 | Feed/detail reads and lifecycle work have independent ownership; cancellation isolation is tested | Pass |
| M17 | Host lifecycle and interruption tests verify safe stop, runtime closure, and retained ownership | Pass |
| M18 | macOS SIGINT test and shared SIGINT/SIGTERM/SIGHUP routing; Linux unavailable as recorded above | Pass on available platform |
| M19 | `HostIntegrationTests.test_recorded_running_state_is_not_claimed_or_dispatched_on_startup` | Pass |
| M20 | Controller hardening accounting cases NC209–NC218 and preparation-lineage reporting cases | Pass |
| M21 | Public JSON/secret test, including recursive secret-key removal | Pass |
| M22 | Independent history-cursor and journal-transition test | Pass |
| M23 | Controller report cases NC218–NC222 and immutable public `run_report` query | Pass |
| M24 | EngineHost lifecycle, detail, report, paging, history, status, intervention, and shutdown tests use no Textual classes | Pass |

## Measured fixture

The specified 1,000-task, 20-session, 200-event fixture was projected 50 times:

- projection latency: p50 12.345 ms, p95 13.363 ms, maximum 14.749 ms;
- commit-to-visible latency over 30 commits: p50 0.152 ms, p95 0.262 ms,
  maximum 0.478 ms;
- idle database statements over the tested idle interval: 0;
- snapshot delivery under a 10,000-update burst: one pending snapshot slot and
  at most one queued wake-up.

The current synchronous check/Git adapters still determine how long shutdown can
wait for a safe point. This is the limitation specified in Part 1 section 9.2:
the pause latch is immediate and the lifecycle waits for the existing operation
boundary before admitting further provider work.
