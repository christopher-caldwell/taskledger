# Product requirement traceability

The acceptance suite exercises the end-to-end flows; unit-level coverage is organized by the same functional ranges below.  The implementation keeps the numbered requirements as the source of truth.

| Product requirements | Automated coverage focus |
|---|---|
| PS-001–PS-003 | Project initialization, detached/mismatched branch handling, canonical branch protection |
| PS-010–PS-014 | Registered specification hashing, change detection, pending review gate, scoped review invalidation |
| PS-020–PS-024 | Immutable requirement revisions, source traceability, direct/task-backed coverage, retirement and supersession history |
| PS-030–PS-033 | Transactional batch materialization, plan fingerprinting, all-at-once structural validation, current-plan gate |
| PS-040–PS-045 | Task lifecycle, revisions, cancellation, reopen, split-by-ordinary-operations |
| PS-050–PS-052 | Dependency reachability and assignment eligibility |
| PS-060–PS-063 | Worktree assignment context, scoped credentials, explicit worker-profile routing, revocation |
| PS-070–PS-073 | Worker questions, blockers, risks, and follow-up proposals |
| PS-080–PS-083 | Checkpoint commits, required-check evidence, immutable submissions, correction/idempotency and routine-escalation rules |
| PS-090–PS-095 | Orchestrator-only verification and accept/reject/blocked transitions |
| PS-100–PS-103 | Exact-commit integration, safe abort, integration reachability invalidation |
| PS-110–PS-112 | Explicit durable blocker scopes and propagation |
| PS-120–PS-122 | Deterministic recovery document and operation journal |
| PS-130–PS-132 | Requirement-based progress derivation |
| PS-140–PS-145 | Scoped requirement verification currency, explicit project completion, and safe finalized-worktree cleanup |
| PS-150–PS-154 | Transactional ledger writes, journalled Git mutations, actionable JSON errors |

## End-to-end scenario map

| Scenario | Public CLI test |
|---|---|
| A, E, L | `test_full_task_backed_flow_and_worker_scope` |
| B | `test_detached_head_is_rejected_without_project_row` |
| C, D | `test_dependency_is_not_satisfied_until_integration` |
| F | `test_rejection_preserves_history_and_allows_correction` |
| G | `test_merge_conflict_is_safely_aborted_and_task_stays_accepted` |
| H | `test_direct_requirement_and_specification_review_gate`, `test_pending_review_retargets_latest_observation` |
| I | `test_direct_requirement_and_specification_review_gate` |
| J, K | `test_recovery_snapshot_and_external_rewrite_remove_credit` |
| Required checks | `test_required_checks_must_be_reported_as_successful_submission_evidence` |
| Routine escalation | `test_second_routine_rejection_requires_a_complex_assignment` |
| Atomic plan batch | `test_plan_apply_batches_new_requirements_tasks_and_local_dependencies` |
| Requirement replacement | `test_requirement_supersession_preserves_completed_historical_tasks` |
