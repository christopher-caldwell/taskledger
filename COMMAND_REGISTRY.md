# Taskledger v1 command registry

All commands emit the stable JSON envelope in Technical Specification §9. Unknown JSON fields are rejected by the request parser. Commands except `project init` require a token; orchestrator commands resolve `--project`, current repository, then the credential file, while worker commands resolve only from the worker credential.

| Command | Role | Request body | Gate |
|---|---|---|---|
| `project init` | none | flags: `repo`, `confirm_branch` | existing non-bare symbolic-HEAD repository |
| `project show`, `project recover`, `project complete` | orchestrator | `{}` | show/recover always; completion requires all conditions |
| `project set-canonical-branch` | orchestrator | `new_branch`, `confirm_branch` | no active work/recovery and integrations reachable |
| `spec register`, `spec check`, `spec review` | orchestrator | path / `{}` / review decision | review maintenance allowed during gates |
| `requirement create`, `update`, `retire`, `list`, `verify`, `invalidate` | orchestrator | complete definition / filter / evidence | verification requires current valid plan |
| `task create`, `update`, `cancel`, `reopen`, `list`, `integrate` | orchestrator | complete definition / action / filter | integration requires accepted exact submission |
| `plan validate` | orchestrator | `{}` | planning maintenance |
| `assignment create`, `revoke`, `rotate-token` | orchestrator | task / assignment+reason / assignment | create requires eligibility |
| `submission verify` | orchestrator | complete independent decision | acceptance requires current plan and no blocker |
| `blocker create`, `resolve`, `list` | orchestrator | scope / resolution / filter | explicit resolution only |
| `question answer`; `follow-up list`, `review` | orchestrator | answer / filter / disposition | plan-neutral |
| `worker context`, `question`, `blocker`, `follow-up`, `submit` | scoped worker | `{}` / worker-local payload | only its active assignment; submission blocked by recovery |
| `operation adopt-success`, `mark-failed` | orchestrator | operation ID and current OID | only deterministic recovery proof |
