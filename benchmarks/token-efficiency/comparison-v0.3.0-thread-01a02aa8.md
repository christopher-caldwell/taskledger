# Taskledger v0.3.0 post-run comparison

> Measurement correction (2026-08-24): the original collector counted parent
> token records copied into full-history worker forks as new usage. Corrected
> broad per-task values are 9.16M to 2.55M for Sol/high (-72.2%), 6.67M to
> 2.75M for Terra/medium (-58.8%), and 15.83M to 5.29M combined (-66.6%). The
> original 80.7% combined estimate is superseded. The qualitative conclusion
> remains directional because task-family confounding is unchanged.

Thread: `01a02aa8-4b53-7f11-ad60-e514a1f21dca`  
Repository: `/Users/christophercaldwell/Code/business/clients/destinations_beyond/db_financial_tracker`  
Post-run window: 2026-08-22T17:57:37Z–2026-08-22T19:18:45Z

## Version verification

This run qualifies as a v0.3.0 sample:

- v0.3.0 was installed at 17:57:37Z; the root task began at 18:07:47Z.
- The rollout injected `/Users/christophercaldwell/.codex/skills/taskledger/SKILL.md` with `metadata.version: "0.3.0"` and the compact-resume/two-approval instructions.
- The task executed `taskledger --version` and received `0.3.0`.
- New response/command behavior appears in the rollout: `context_hash`, `dynamic_hash`, path-only worker credentials, `project resume`, and `submission review-context`.
- Assignment responses contained `worker_token_path` and `context_hash`, with no plaintext `worker_token` or duplicate full context.

## Broad normalized result (context only)

This original comparison excludes baseline auto-review and the unmatched Sol/medium cohort. Values are normalized by integrated task: 22 baseline tasks versus 6 post-v0.3.0 tasks. It is retained for reproducibility, but it is **not the primary effectiveness estimate**: the 22-task baseline mixes application work, PostgreSQL architecture, authorization, and HTTP work, while the post sample is the tail of one already-planned HTTP queue.

| Measure per integrated task | Pre-v0.3.0 | Post-v0.3.0 | Change |
|---|---:|---:|---:|
| Sol/high input tokens | 9.16M | 2.55M | **-72.2%** |
| Sol/high cached input | 8.92M | 2.47M | **-72.3%** |
| Sol/high output tokens | 24,303 | 8,066 | **-66.8%** |
| Sol/high token events | 74.9 | 23.8 | **-68.2%** |
| Sol/high compactions | 0.50 | 0.17 | **-66.7%** |
| Terra/medium input tokens | 6.67M | 2.75M | **-58.8%** |
| Terra/medium cached input | 6.47M | 2.64M | **-59.3%** |
| Terra/medium output tokens | 35,980 | 17,656 | **-50.9%** |
| Terra/medium token events | 73.4 | 33.2 | **-54.8%** |
| Matched Sol/high + Terra/medium input | 15.83M | 5.29M | **-66.6%** |

## Same-spec task-level check

`financial-tracker-http-spec-task-complexity.json` reconstructs all 11 tasks in the HTTP specification from each assignment's activation through its successful integration. It includes exact-model token events, Git diff size, criteria, requirements, submissions, rejected reviews, and elapsed execution window.

| Cohort inside the same specification | Tasks | Criteria | Files | Changed lines | Submissions / rejected | Matched Sol/high + Terra/medium input |
|---|---:|---:|---:|---:|---:|---:|
| Earlier five tasks | 5 | 28 | 116 | 6,048 | 10 / 5 | 57.80M |
| Later six tasks | 6 | 31 | 50 | 5,255 | 8 / 2 | 35.75M |
| Earlier HTTP-only tasks | 3 | 17 | 49 | 4,562 | 8 / 5 | 41.59M |
| Later clean endpoint tasks | 4 | 20 | 36 | 4,063 | 5 / 1 | 19.96M |

The last row excludes insurance because its assignment began under the old setup, was rejected, sat idle, and was corrected after v0.3.0; it is a version-spanning task, not a clean post sample. It also excludes the final assembled verification gate because a cross-cutting verification task is not comparable to an endpoint implementation.

For the least-bad within-spec contrast, matched input per changed line fell from
9,117 for the three earlier HTTP tasks to 4,914 for the four later clean
endpoint tasks, a 46.1% reduction. Input per criterion fell 59.2%. These are
sensitivity checks, not causal estimates: reference resources and trip core are
inherently broader than repetitive add-on endpoints, while Git lines do not
measure architectural difficulty.

The narrow sibling check is still useful but less stable after correction: the
three clean v0.3.0 add-ons (activity, transportation, hotel stay) changed 1,193,
1,202, and 1,101 lines and consumed 6.56M, 4.10M, and 4.60M input tokens. No
equivalent pre-v0.3.0 sibling exists for a direct A/B.

## Workflow and quality signals

| Signal per integrated task | Pre-v0.3.0 | Post-v0.3.0 | Change |
|---|---:|---:|---:|
| `project recover` calls | 4.91 | 0.17 | **-96.6%** |
| `worker context` calls | 4.95 | 1.33 | **-73.1%** |
| Submission rejection rate | 36.1% (13/36) | 14.3% (1/7) | Improved |
| Uncertain/failed post-run operations | — | 0 | No regression signal |

The post run used two compact resumes, one full cold-start recovery, eight focused review-context reads for seven submissions, and one primary compaction. All six tasks integrated, eleven requirements were verified, five stale blockers were resolved with proof, and project completion succeeded.

## Interpretation

This is directional evidence that v0.3.0 reduced context replay. The largest improvements occur where expected: primary Sol/high input, broad recovery calls, token-event count, and compactions. The task-level sensitivity check remains favorable after accounting for observable work size, but task-type confounding is still substantial.

It is not yet causal proof:

- The baseline covers 22 tasks across broader planning/execution phases; the post sample is six related HTTP tasks in an already-approved sequential queue.
- The post prompt correctly skipped planning approvals because the plan was already materialized, so this sample cannot measure the seven-to-two approval change.
- Baseline auto-review used 112.55M input tokens; the post task used user approval rather than auto-review. Auto-review is excluded from the matched result because that configuration change is not a Taskledger v0.3.0 effect.
- Logged command counts are lower bounds when shell helper functions hide the literal command; durable database event counts are authoritative for completed work.
- Task difficulty and test duration were not randomized.

## Conclusion

The predefined materiality threshold was a 20% matched-model reduction with
non-inferior quality. Every corrected normalization remains above that
threshold, and the workflow-specific mechanisms moved in the expected
direction. However, the corrected 66.6% per-task figure still overstates
precision because the task portfolios differ. Classify the result as
**promising directional confirmation**, not final statistical proof. The next
new-spec run should be evaluated using the predeclared task-family protocol in
`task-complexity-methodology.md`; the broad 22-versus-6 comparison must remain
contextual only.

One small residual inefficiency appeared: the task attempted unsupported `project status` twice. The skill/command reference should continue steering routine state checks to `project resume` rather than invented status commands.
