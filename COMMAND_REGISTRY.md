# Taskledger command registry

This table is the exhaustive public command registry. Taskledger has no
interactive `--help` contract; callers must not probe unlisted commands or flags.

All commands emit the stable JSON envelope in Technical Specification §9. Unknown JSON fields are rejected by the request parser. Commands except `project init` require a token; orchestrator commands resolve `--project`, current repository, then the credential file, while worker commands resolve only from the worker credential.

| Command | Role | Request body | Gate |
|---|---|---|---|
| `project init` | none | flags: `repo`, `confirm_branch` | existing non-bare symbolic-HEAD repository |
| `project show`, `project recover`, `project complete`, `project cleanup` | orchestrator | `{}` | completion requires all conditions and then safely cleans finalized worktrees; cleanup retries only after completion |
| `project resume` | orchestrator | `{}` or `{"cursor":"sha256"}` | compact state refresh; response identifies when full recovery is required |
| `project set-canonical-branch` | orchestrator | `new_branch`, `confirm_branch` | no active work/recovery and integrations reachable |
| `spec register`, `spec check`, `spec review` | orchestrator | path / `{}` / review decision | review maintenance allowed during gates |
| `requirement create`, `update`, `retire`, `supersede`, `list`, `verify`, `invalidate` | orchestrator | complete definition / replacement / filter / evidence | supersession preserves completed history; verification requires current valid plan |
| `task create`, `update`, `cancel`, `reopen`, `list`, `integrate` | orchestrator | complete definition / action / filter | integration requires accepted exact submission |
| `plan apply`, `validate` | orchestrator | new-item batch / `{}` | apply is transactional; validation remains explicit |
| `assignment create`, `revoke`, `rotate-token` | orchestrator | task+worker profile / assignment+reason / assignment | create requires eligibility; a twice-rejected routine task requires `complex` |
| `submission review-context`, `verify` | orchestrator | submission / complete independent decision | review context binds exact OIDs and claims; acceptance requires current plan and no blocker |
| `blocker create`, `resolve`, `list` | orchestrator | scope / resolution / filter | explicit resolution only |
| `question answer`; `follow-up list`, `review` | orchestrator | answer / filter / disposition | plan-neutral |
| `worker context`, `question`, `blocker`, `follow-up`, `submit` | scoped worker | `{}` / worker-local payload | only its active assignment; submission requires successful evidence for every task `required_check` |
| `operation adopt-success`, `mark-failed` | orchestrator | operation ID and current OID | only deterministic recovery proof |
