# Taskledger command registry

This table is the exhaustive public command registry. Taskledger has no
interactive `--help` contract; callers must not probe unlisted commands or flags.

All commands emit the stable JSON envelope in Technical Specification §9. Unknown JSON fields are rejected by the request parser. Commands except `project init` require a token; orchestrator commands resolve `--project`, current repository, then the credential file, while worker commands resolve only from the worker credential.

| Command | Role | Request body | Gate |
|---|---|---|---|
| `project init` | none | flags: `repo`, `confirm_branch` | existing non-bare symbolic-HEAD repository |
| `project show`, `project recover`, `project complete`, `project cleanup` | orchestrator | `{}` | completion requires all conditions and then safely cleans finalized worktrees; cleanup retries only after completion |
| `project resume`, `wait`, `preflight` | orchestrator | cursor refresh / bounded event filter / declared prerequisites and host report | wait uses audit sequences; profile config is not host runtime proof |
| `project set-canonical-branch` | orchestrator | `new_branch`, `confirm_branch` | no active work/recovery and integrations reachable |
| `spec register`, `spec check`, `spec review` | orchestrator | path / `{}` / review decision | review maintenance allowed during gates |
| `requirement create`, `update`, `retire`, `supersede`, `list`, `verify`, `invalidate` | orchestrator | complete definition / replacement / filter / evidence | supersession preserves completed history; verification requires current valid plan |
| `task create`, `update`, `cancel`, `reopen`, `list`, `integrate` | orchestrator | complete definition / action / filter | integration requires accepted exact submission |
| `plan apply`, `validate` | orchestrator | new-item batch / `{}` | apply is transactional; validation remains explicit |
| `assignment create`, `revoke`, `rotate-token` | orchestrator | task+worker profile+optional mode/checkpoints / assignment+reason / assignment | lightweight mode retains one worker through approved vertical checkpoints |
| `checkpoint review-context`, `verify` | orchestrator | checkpoint / criteria+decision | intermediate approval binds an immutable commit and never integrates or accepts final work |
| `submission review-context`, `check`, `verify` | orchestrator | submission / exact declared check / complete independent decision | acceptance needs independent reviewer receipts for declared checks |
| `artifact register`, `list`; `evidence export` | orchestrator | repository file / `{}` / optional task filter | retained hashed artifacts and deterministic provenance-separated export |
| `blocker create`, `resolve`, `list` | orchestrator | scope / resolution / filter | explicit resolution only |
| `question answer`; `follow-up list`, `review` | orchestrator | answer / filter / disposition | plan-neutral |
| `worker context`, `check`, `checkpoint`, `artifact-register`, `question`, `blocker`, `follow-up`, `submit` | scoped worker | cached context / exact check / slice / local file / worker-local payload | correction/checkpoint state is durable; receipts and artifacts remain assignment-scoped |
| `operation adopt-success`, `mark-failed` | orchestrator | operation ID and current OID | only deterministic recovery proof |
| `controller run-assignment` | orchestrator | assignment ID, `live=true`, optional durable limits | foreground supervision of one existing assignment; live execution requires explicit opt in |
| `controller resume` | orchestrator | run ID, `live=true` | reconcile recorded thread and turn identities before continuing |
| `controller show` | orchestrator | run ID | read controller state, sessions, pause reason, and usage without dispatch |
| `controller report` | orchestrator | run ID, optional provider detail | deterministic local report; reads Codex persisted history but never starts a model turn |
| `controller extend-budget` | orchestrator | run ID, grant kind, positive amount, reason | audited extension without resetting prior turns or usage |
