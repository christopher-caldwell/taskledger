# Taskledger command reference

All commands return one JSON envelope on stdout. Success is `{"ok":true,"command":"...","data":...,"warnings":[]}`. Failure is `{"ok":false,"command":"...","error":...}`.

Use `taskledger <resource> <action> --input -` and supply exactly one JSON object on stdin. Read-only commands accept `{}` unless a filter is shown.

## Project and specification

- `project init --repo <absolute-path>`: detect the symbolic branch without writing ledger state.
- `project init --repo <absolute-path> --confirm-branch <exact-name>`: initialize.
- `project show`, `project recover`, `project complete`: `{}`.
- `project set-canonical-branch`: `{"new_branch":"release/next","confirm_branch":"release/next"}`.
- `spec register`: `{"relative_path":"docs/product-spec.md"}`.
- `spec check`: `{}`.
- `spec review`: `{"review_id":"uuid","resolution":"APPROVE_REVISION","affected_requirement_ids":[],"affected_task_ids":[],"no_existing_items_affected":true,"summary":"No existing item is affected."}`. Use `RETIRE_SPECIFICATION` only for intentional removal.

## Requirements and tasks

- `requirement create`: `{"statement":"Observable behavior","details":"Independent detail","implementation_required":true,"sources":[{"specification_id":"uuid","locator":"Section X","excerpt":null}]}`.
- `requirement update`: complete replacement definition plus `requirement_id` and `assignment_dispositions`. Each affected active assignment needs `{"assignment_id":"uuid","action":"CONTINUE"}` or `REVOKE`.
- `requirement retire`: `{"requirement_id":"uuid","reason":"Why retired","assignment_dispositions":[]}`.
- `requirement list`: `{"filter":"all"}`; filters are `all`, `complete`, `remaining`, `blocked`.
- `requirement verify`: `{"requirement_id":"uuid","evidence":[{"label":"Behavior check","details":"What was checked and the result"}],"notes":"Why this requirement is satisfied"}`.
- `requirement invalidate`: `{"requirement_id":"uuid","reason":"Why current verification is stale"}`.
- `task create`: `{"objective":"Bounded outcome","implementation_scope":"Files and behavior in scope","acceptance_criteria":["Observable criterion"],"requirement_ids":["uuid"],"dependency_task_ids":[]}`.
- `task update`: complete replacement task definition plus `task_id`; add `assignment_action` as `CONTINUE` or `REVOKE` when an active assignment exists.
- `task cancel`: `{"task_id":"uuid","reason":"Why cancelled","revoke_assignment":false,"submission_action":null}`. Use the explicit disposition fields required by current state.
- `task reopen`: complete replacement task definition plus `task_id`.
- `task list`: `{"filter":"eligible"}`; filters are `all`, `eligible`, `active`, `submitted`, `accepted`, `completed`, `blocked`, `cancelled`.
- `plan validate`: `{}`.

## Assignment and worker

- `assignment create`: `{"task_id":"uuid"}`.
- `assignment revoke`: `{"assignment_id":"uuid","reason":"Why revoked"}`.
- `assignment rotate-token`: `{"assignment_id":"uuid"}`.
- `worker context`: `{}`.
- `worker question`: `{"body":"Question text","blocking":true}`.
- `worker blocker`: `{"category":"EXTERNAL_DEPENDENCY","scope_type":"TASK","description":"What is blocked"}`. Worker scope is limited to `TASK` or `ASSIGNMENT`; Taskledger derives the ID.
- `worker follow-up`: `{"body":"Possible later work"}`.
- `worker submit`: `{"summary":"Implementation summary","evidence":[{"label":"Tests","details":"Relevant tests passed","command":"python -m unittest","exit_code":0,"artifact_path":null}],"risks":[],"unresolved_questions":[],"follow_up_work":[]}`.

For each risk use `{"description":"Known risk","blocking":false,"blocker_category":null}`. For each unresolved question use `{"body":"Question","blocking":false,"blocker_category":null}`. When `blocking` is true, provide a supported category.

Worker calls use `--token <scoped-worker-token>` and resolve no project from the current directory.

## Verification, integration, and blockers

- `submission verify`: `{"submission_id":"uuid","outcome":"ACCEPTED","criterion_results":[{"criterion_id":"uuid","satisfied":true,"evidence":"Independent check"}],"behavior_matches_intent":true,"required_evidence_present":true,"blocking_issues_remaining":false,"corrections":null,"notes":"Verification summary"}`. Include every current criterion exactly once. `REJECTED` requires corrections and at least one unmet assertion. `BLOCKED` requires exactly one `blocker_id` or `blocker` definition.
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

Exit codes: `0` success, `2` invalid request, `3` state gate, `4` auth failure, `5` known-safe Git failure, `6` recovery required, `70` unexpected internal error.
