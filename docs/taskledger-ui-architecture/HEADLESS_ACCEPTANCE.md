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
  # Ran 195 tests in 72.241s; OK (skipped=11)
  ```

- Focused application and lifecycle regression:

  ```sh
  TASKLEDGER_LIVE_CODEX=0 PYTHONPATH=src python3 -m unittest tests.test_console_application tests.test_initial_planning tests.test_controller tests.test_project_controller tests.test_controller_hardening -q
  # Ran 128 tests in 30.901s; OK (skipped=1)
  ```

- Static verification:

  ```sh
  PYTHONPATH=src python3 -m compileall -q src
  git diff --check
  ```

No live provider execution was used. Platform-specific signal behavior and the
portable subprocess paths are covered by the existing automated suites; Linux
is not a remaining-work item for this headless acceptance pass.

## Mandatory mechanics matrix

| Case | Automated evidence | Result |
|---|---|---|
| M01 | `HostIntegrationTests.test_cli_import_does_not_import_textual`; missing-extra CLI tests in `test_console_ui` | Pass |
| M02 | Full legacy acceptance suite; initial-planning CLI wrapper tests | Pass |
| M03 | `test_controller`, `test_project_controller`, and `test_controller_hardening` fake-runtime suites | Pass |
| M04 | `ControllerTests.test_observer_variants_preserve_controller_semantics` compares the complete deterministic controller trace under no, fast, slow, and failing observers | Pass |
| M05 | `CommittedNotificationTests.test_worker_broker_isolated_connection_inherits_owner_observer`, `HostIntegrationTests.test_real_worker_question_and_blocker_refresh_host_snapshot`, and the real broker question/submit mutations in `InitialPlanningTests.test_engine_host_public_contract_pauses_during_integration_and_resumes_to_completion` | Pass |
| M06 | `CommittedNotificationTests` rollback, visibility, and observer-failure cases | Pass |
| M07 | `HostIntegrationTests.test_snapshot_and_paged_queries_are_pure_under_write_denial` | Pass |
| M08 | `FeedTests.test_atomic_seed_and_burst_coalescing` | Pass |
| M09 | The same test publishes 10,000 revisions into one pending slot; history reads durable streams independently | Pass |
| M10 | `FeedTests.test_cancelled_wait_does_not_close_feed`; engine operations use strong task ownership and shielded admission waits | Pass |
| M11 | `HostIntegrationTests.test_engine_host_projects_real_ledger_on_owner_thread` | Pass |
| M12 | Registry-capacity tests plus stale-specification, proposal-hash, active-run, and project-lock tests | Pass |
| M13 | Initial-planning ambiguity, preflight, specification-mutation, diagnostic-failure, and attempt-lineage tests | Pass |
| M14 | Worker/reviewer/planner admission latches; `InitialPlanningTests.test_pause_latch_is_set_during_a_synchronous_safe_boundary`; `test_pause_during_actual_reviewer_execution_reconciles_and_pauses`; and the integration-`STARTED` pause boundary in the strict public-contract fixture | Pass |
| M15 | Controller uncertain-outcome and dispatch-boundary recovery tests | Pass |
| M16 | Feed/detail reads and lifecycle work have independent ownership; cancellation isolation is tested | Pass |
| M17 | `HostIntegrationTests.test_fatal_primary_client_closes_owned_lifecycle_and_durable_resources` verifies fatal-client cleanup closes the fake runtime and database, releases the lease, durably pauses/reconciles the run, and exits the engine thread; focused close-latch tests cover retry behavior | Pass |
| M18 | macOS SIGINT test, shared SIGINT/SIGTERM/SIGHUP routing, and portable signal/subprocess coverage | Pass |
| M19 | `HostIntegrationTests.test_recorded_running_state_is_not_claimed_or_dispatched_on_startup` | Pass |
| M20 | Controller hardening accounting cases NC209–NC218 and preparation-lineage reporting cases | Pass |
| M21 | Public JSON/secret test, including recursive secret-key removal | Pass |
| M22 | Independent history-cursor and journal-transition test | Pass |
| M23 | Controller report cases NC218–NC222 and immutable public `run_report` query | Pass |
| M24 | `InitialPlanningTests.test_engine_host_public_contract_pauses_during_integration_and_resumes_to_completion` proves prepare, exact detail, cancelled receipt wait plus status lookup, approve/start, worker, reviewer, integration, pause, durable `PAUSED`, intervention, resume, `COMPLETED`, task detail, history paging, and run reporting through `EngineHost` only | Pass |

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
