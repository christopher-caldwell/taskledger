# Taskledger command reference

Machine commands return one JSON envelope on stdout. Success is `{"ok":true,"command":"...","data":...,"warnings":[]}`. Failure is `{"ok":false,"command":"...","error":...}`.

This file is the exhaustive public command and input registry. The CLI does not
provide an interactive help surface; do not probe unlisted commands or flags.

`taskledger --version` returns the installed version in the same JSON envelope.

Human commands use prompts and plain text:

- `taskledger bootstrap`
- `taskledger prepare --spec <repository-path>`
- `taskledger start`
- `taskledger ui`

Model-safe bootstrap is `taskledger bootstrap --confirm-branch <exact-name> --commit-setup`. It requires a clean repository, installs only missing setup, commits only generated setup paths as `Configure Taskledger`, and never overwrites a profile. This mode is intended for explicit `$taskledger` LOAD; ordinary human bootstrap remains interactive.

Use `taskledger <resource> <action> --input -` and supply exactly one JSON object on stdin. Read-only commands accept `{}` unless a filter is shown.

## Project and specification

- `project init --repo <absolute-path>`: detect the symbolic branch and repository-local ledger without writing state.
- `project init --repo <absolute-path> --confirm-branch <exact-name>`: initialize.
- `project prepare`: `{"spec_path":"docs/product-spec.md","run_group_id":"optional-prior-group-uuid","live":true,"limits":{"max_initial_planner_turns":2,"max_total_worker_turns":30,"max_total_reviewer_turns":20,"max_total_task_creator_turns":4,"max_worker_turns_per_assignment":8,"max_reviewer_turns_per_submission":2,"max_final_reviewer_turns":2,"max_total_tokens":2000000,"turn_timeout_seconds":1800,"max_elapsed_seconds":14400,"max_workers":1,"max_reviewers":1},"preflight":{"local_inputs":[],"services":[]}}`. Initializes when needed, validates the repository and profiles, runs one bounded read-only Task Creator, stores an immutable proposal, and stops at `AWAITING_APPROVAL`. It returns a generated `run_group_id`; reuse that exact ID for a retry in the same benchmark/trial. Preflight failures are retained as zero-model attempts. `live:true` is required because planning invokes a model.
- `project start`: `{"preparation_id":"uuid","approve_proposal_hash":"sha256:...","live":true}`. Revalidates the complete preparation identity, materializes the exact proposal, and starts the foreground project controller. It binds the planner to stored specification bytes and rejects changed, missing, inactive, or legacy noncanonical source proposals before model dispatch. Drift fails as `PREPARATION_STALE`.
- `project show`, `project recover`, `project complete`, `project cleanup`: `{}`. Completion records the canonical commit, then removes clean finalized assignment worktrees. Cleanup retries that removal for an already-completed project. Both retain assignment branches; unsafe worktrees are reported and skipped.
- `project resume`: `{}` for a compact baseline or `{"cursor":"sha256"}` for a conditional refresh. A response with `full_recovery_required` requires `project recover`.
- `project wait`: `{"cursor":123,"event_types":["SUBMISSION_RECORDED","CHECKPOINT_RECORDED","WORKER_QUESTION_CREATED"],"timeout_ms":30000,"limit":50}`. The cursor is the `latest_event_sequence` returned by `project resume` or the prior wait. Waiting is bounded to 60 seconds, rejects future cursors, reports the retained event floor and no-pruning status, and requires the host to wake the model after the CLI returns.
- `project preflight`: `{"profiles":["routine","complex"],"local_inputs":[{"name":"test environment","path":"/safe/path","required":true}],"services":[{"name":"postgres","host":"127.0.0.1","port":5432,"required":true}],"host_agent_availability":{"routine":true,"complex":true}}`. File contents and secrets are not read. Profile files prove configuration only; host availability is a labeled host report, not runtime proof.
- `project set-canonical-branch`: `{"new_branch":"release/next","confirm_branch":"release/next"}`.
- `spec register`: `{"relative_path":"docs/product-spec.md"}`.
- `spec check`: `{}`.
- `spec review`: `{"review_id":"uuid","resolution":"APPROVE_REVISION","affected_requirement_ids":[],"affected_task_ids":[],"no_existing_items_affected":true,"summary":"No existing item is affected."}`. Use `RETIRE_SPECIFICATION` only for intentional removal.

## Requirements and tasks

- `requirement create`: `{"statement":"Observable behavior","details":"Independent detail","implementation_required":true,"sources":[{"specification_id":"uuid","locator":"Section X","excerpt":null}]}`. A requirement stores one source per specification and locator. Repeated entries are normalized in first-seen order: distinct non-null excerpts are joined with two newlines, and an all-null group remains null.
- `requirement update`: complete replacement definition plus `requirement_id` and `assignment_dispositions`. Each affected active assignment needs `{"assignment_id":"uuid","action":"CONTINUE"}` or `REVOKE`.
- `requirement retire`: `{"requirement_id":"uuid","reason":"Why retired","assignment_dispositions":[]}`.
- `requirement supersede`: `{"requirement_id":"old-uuid","reason":"Replacement reason","replacement":{"statement":"New behavior","details":"Observable details","implementation_required":true,"sources":[{"specification_id":"uuid","locator":"Section Y"}]},"assignment_dispositions":[]}`. This atomically creates the replacement and retires the old requirement without rewriting completed historical tasks.
- `requirement list`: `{"filter":"all"}`; filters are `all`, `complete`, `remaining`, `blocked`, `ready`.
- `requirement verify`: `{"requirement_id":"uuid","evidence":[{"label":"Behavior check","details":"What was checked and the result"}],"notes":"Why this requirement is satisfied"}`.
- `requirement invalidate`: `{"requirement_id":"uuid","reason":"Why current verification is stale"}`.
- `task create`: `{"objective":"Bounded outcome","implementation_scope":"Files and behavior in scope","acceptance_criteria":["Observable criterion"],"required_checks":["python3 -m unittest"],"requirement_ids":["uuid"],"dependency_task_ids":[]}`. `required_checks` is optional. Each exact command must be executed through `worker check`, linked by receipt at submission, and rerun through `submission check` before acceptance.
- `task update`: complete replacement task definition plus `task_id`; add `assignment_action` as `CONTINUE` or `REVOKE` when an active assignment exists.
- `task cancel`: `{"task_id":"uuid","reason":"Why cancelled","revoke_assignment":false,"submission_action":null}`. Use the explicit disposition fields required by current state.
- `task reopen`: complete replacement task definition plus `task_id`.
- `task list`: `{"filter":"eligible"}`; filters are `all`, `eligible`, `active`, `submitted`, `accepted`, `completed`, `blocked`, `cancelled`.
- `plan apply`: transactionally creates a new plan batch using local `ref` values. Requirements use the `requirement create` fields plus `ref`. Tasks use `ref`, the task-definition fields, `requirement_refs`, and `dependency_refs`; optional `requirement_ids` and `dependency_task_ids` may point to existing project items. Local refs are resolved to returned UUID mappings. This command creates only new items and does not validate the plan.
- `plan validate`: `{}`.
- `plan preview`: accepts `{"spec_path":"spec.md","proposal":<proposal>,"limits":<optional-project-prepare-limits>,"preflight":{"local_inputs":[],"services":[]}}`. The proposal contains exactly `requirements`, `tasks`, `execution_policy`, `assumptions`, and `ambiguities` using the shapes below. Preview normalizes and validates it, resolves the exact Git/spec/profile/configuration envelope, and returns `rendered_plan`, `normalized_proposal`, `proposal_hash`, and `plan_hash` without changing specifications, tasks, preparations, sessions, or controller runs. A valid but ambiguous proposal returns `NEEDS_REVISION` and cannot be committed.
- `plan commit`: accepts the identical preview request plus `"approve_plan_hash":"sha256:..."`. It recomputes the entire envelope and rejects drift before mutation. On success it registers or activates the exact specification revision, stores an `INVOKING_MODEL` preparation, records its exact operator approval, returns `READY`, and does not materialize tasks or start a controller. Active assignments or a running controller reject this path.

The model proposal shapes are:

- Requirement: `{"ref":"R1","statement":"Observable behavior","details":"Independent detail","implementation_required":true,"sources":[{"locator":"Section X","excerpt":null}]}`.
- Task: `{"ref":"T1","objective":"Bounded outcome","implementation_scope":"Owned files and behavior","acceptance_criteria":["Observable criterion"],"required_checks":["python3 -m unittest"],"requirement_refs":["R1"],"dependency_refs":[]}`.
- Execution policy: `{"task_ref":"T1","wave":1,"worker_profile":"routine","parallel_safe":true,"write_surfaces":["src/example.py"]}`.

## Assignment and worker

- `assignment create`: `{"task_id":"uuid","worker_profile":"routine","execution_mode":"lightweight","checkpoints":[{"label":"First vertical slice","criteria":["The slice works end to end"]}]}`. `execution_mode` defaults to `isolated`. `lightweight` requires at least one ordered checkpoint and retains the same scoped assignment/worktree across slices. Checkpoint count, worker profile, task granularity, and assignment isolation are independent choices.
- `assignment revoke`: `{"assignment_id":"uuid","reason":"Why revoked"}`.
- `assignment rotate-token`: `{"assignment_id":"uuid"}`.
- Assignment creation and rotation return only `worker_token_path`; read the credential from that owner-only file. Creation also returns `context_hash`, not the full assignment snapshot.
- `worker context`: `{}` or `{"if_none_match":"context-hash","if_dynamic_none_match":"dynamic-hash"}`. The static assignment snapshot and live coordination overlay are cached independently. Dynamic context includes the latest applicable submission correction packet and durable checkpoint progression. Correction packets bind the reviewed submission and commit, failed criteria, exact corrections, blockers, verification revision, and packet hash. A newer pending submission supersedes older feedback.
- `worker check`: `{"command":"python3 -m unittest","timeout_seconds":900}`. Executes only an exact declared required check in the assignment worktree, commits current worker changes first, retains bounded combined output, and records source revision/tree fingerprints, timing, exit status, and stale-source status. Timeout is 1–3600 seconds.
- `worker checkpoint`: `{"summary":"Slice complete","evidence":[{"label":"Focused test","details":"Observed behavior","receipt_id":"uuid"}]}`. Records an immutable commit for the next checkpoint. A pending checkpoint blocks further progression until orchestrator review; rejection unlocks repair of the same slice.
- `worker artifact-register`: `{"path":"relative/path"}`. Copies one intentional regular file from the assignment worktree into ledger-managed retained storage with hash and provenance. Symlinks, traversal, directories, and files over 50 MiB are rejected.
- `worker question`: `{"body":"Question text","blocking":true}`.
- `worker blocker`: `{"category":"EXTERNAL_DEPENDENCY","scope_type":"TASK","description":"What is blocked"}`. Worker scope is limited to `TASK` or `ASSIGNMENT`; Taskledger derives the ID.
- `worker follow-up`: `{"body":"Possible later work"}`.
- `worker submit`: `{"summary":"Implementation summary","evidence":[{"label":"Tests","details":"Observed required check","receipt_id":"uuid","artifact_id":"uuid"}],"risks":[],"unresolved_questions":[],"follow_up_work":[]}`. All planned checkpoints must be approved. Prose command/exit-code claims remain claims and never satisfy a required check.

After a routine assignment's second rejected submission, Taskledger revokes that assignment and rejects any new `routine` assignment for the task with `WORKER_PROFILE_ESCALATION_REQUIRED`. Create the next assignment with `worker_profile: "complex"`.

For each risk use `{"description":"Known risk","blocking":false,"blocker_category":null}`. For each unresolved question use `{"body":"Question","blocking":false,"blocker_category":null}`. When `blocking` is true, provide a supported category.

Worker calls use `--token <scoped-worker-token>`. Run them from the assignment worktree so the CLI can locate the canonical repository's `.taskledger/`; the token alone determines project and assignment authority.

## Executable controller

- `controller run-assignment`: `{"assignment_id":"uuid","live":true,"limits":{"max_worker_turns":20,"max_consecutive_stalled_turns":2,"max_consecutive_runtime_failures":2,"max_reviewer_turns_per_submission":2,"max_total_tokens":40000,"max_elapsed_seconds":3600,"turn_timeout_seconds":1800,"reviewer_profile":"taskledger_reviewer"}}`. This is an explicit paid live run. It supervises one existing assignment through confirmed integration or a durable pause.
- `controller run-project`: `{"execution_policy":[{"task_id":"uuid","wave":1,"worker_profile":"routine","parallel_safe":true,"write_surfaces":["src/example.py"]}],"live":true,"limits":{"max_workers":2,"max_reviewers":1,"max_inflight_targets":3,"max_total_worker_turns":100,"max_total_reviewer_turns":50,"max_total_task_creator_turns":6,"reviewer_token_reserve":10000,"max_final_reviewer_turns":2,"max_task_creator_turns":2,"task_creator_profile":"taskledger_task_creator","supervisor":{"max_worker_turns":20,"max_consecutive_stalled_turns":2,"max_consecutive_runtime_failures":2,"max_reviewer_turns_per_submission":2,"max_total_tokens":40000,"max_elapsed_seconds":3600,"turn_timeout_seconds":1800,"reviewer_profile":"taskledger_reviewer"}}}`. The approved policy must cover every unfinished task exactly once. Python schedules it through final integrated review, requirement verification, and project completion or a durable pause. The default inflight-target bound is the explicit sum of worker and reviewer slots so review can pipeline with independent worker work.
- `controller resume`: `{"run_id":"uuid","live":true}`. Resumes assignment or project mode and reconciles recorded thread and turn identities before any new dispatch.
- `controller show`: `{"run_id":"uuid"}`. Reads controller run, targets, resolved runtime identities, sessions, pause state, and complete or explicitly incomplete usage totals without dispatching work.
- `controller report`: `{"run_id":"uuid","include_provider_detail":true}`. Produces deterministic structured reporting from Taskledger data and optional Codex persisted-history enrichment. It never starts a model turn and still succeeds with provider detail unavailable. Execution reports retain the direct planning subtotal and report frozen run-group attempts separately; zero-model failures are distinct from missing or incomplete usage.
- `controller extend-budget`: `{"run_id":"uuid","kind":"WORKER_TURNS","amount":2,"reason":"Approved continuation"}`. Kinds are `WORKER_TURNS`, `REVIEWER_TURNS`, `TASK_CREATOR_TURNS`, `TOKENS`, and `ELAPSED_SECONDS`.

Controller workers use assignment-scoped app-server dynamic tools backed by the local broker instead of receiving a worker or orchestrator credential. Unknown runtime outcomes pause and cannot be converted into retry or success by a controller command.

## Verification, integration, and blockers

- `submission verify`: `{"submission_id":"uuid","outcome":"ACCEPTED","criterion_results":[{"criterion_id":"uuid","satisfied":true,"evidence":"Independent check"}],"behavior_matches_intent":true,"required_evidence_present":true,"blocking_issues_remaining":false,"corrections":null,"notes":"Verification summary"}`. Include every current criterion exactly once. `REJECTED` requires corrections and at least one unmet assertion. `BLOCKED` requires exactly one `blocker_id` or `blocker` definition.
- `submission review-context`: `{"submission_id":"uuid"}`. This orchestrator-only packet binds exact OIDs and claims but never replaces independent diff inspection and tests.
- `submission check`: `{"submission_id":"uuid","command":"python3 -m unittest","timeout_seconds":900}`. Runs an exact required check only from the clean assignment worktree at the submitted commit and records a `REVIEWER` receipt. Worker receipts never satisfy this obligation. Commands that change source are recorded stale.
- `checkpoint review-context`: `{"checkpoint_id":"uuid"}`. Binds the intermediate checkpoint to its exact commit, criteria, worker claim, assigned task revision, and current task revision. It does not authorize integration.
- `checkpoint verify`: `{"checkpoint_id":"uuid","outcome":"APPROVED","criterion_results":[{"position":1,"satisfied":true,"evidence":"Exact commit checked"}],"corrections":null,"notes":"Approved"}`. `REJECTED` requires a failed criterion and exact corrections. Task/specification revision dispositions supersede prior approvals; final acceptance remains separate.
- `artifact register`: `{"path":"relative/path"}`; `artifact list`: `{}`. Orchestrator registration is limited to intentional regular files inside the canonical repository. Retained copies survive assignment worktree cleanup.
- `evidence export`: `{}` or `{"task_id":"uuid"}`. Writes deterministic canonical JSON in ledger-managed storage. It preserves registered source-use claims with location/revision/hash and distinguishes worker claims, observed executions, and reviewer conclusions. It generates no semantic conclusion and does not treat delivery/read telemetry as understanding.
- `task integrate`: `{"task_id":"uuid"}`.
- `blocker create`: `{"category":"EXTERNAL_DEPENDENCY","scope_type":"PROJECT","scope_id":null,"description":"What is blocked"}`.
- `blocker resolve`: `{"blocker_id":"uuid","resolution":"How it was resolved"}`.
- `blocker list`: `{"state":"open"}`; use `all` for history.
- `question answer`: `{"question_id":"uuid","answer":"Decision","resolve_blocker":true,"resolution":"Decision supplied"}`.
- `follow-up list`: `{"state":"PROPOSED"}`.
- `follow-up review`: `{"follow_up_id":"uuid","state":"ACKNOWLEDGED"}` or `DISMISSED`.
- `operation adopt-success`: `{"operation_id":"uuid","current_oid":"40-char-git-oid"}`.
- `operation mark-failed`: the same shape; use only after the repository is proven restored to the expected pre-operation OID.

Supported blocker categories: `MISSING_PRODUCT_DECISION`, `AMBIGUOUS_REQUIREMENT`, `EXTERNAL_DEPENDENCY`, `REPOSITORY_STATE`, `VERIFICATION_FAILURE`, `SPECIFICATION_STATE`, `INTERRUPTED_OPERATION`.

Exit codes: `0` success, `2` invalid request, `3` state/storage gate, `4` auth failure, `5` known-safe Git failure, `6` an interrupted operation needs reconciliation, `70` unexpected internal error. Only exit code `6` implies actual recovery work.
