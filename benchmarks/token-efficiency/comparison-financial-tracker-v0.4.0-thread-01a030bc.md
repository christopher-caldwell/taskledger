# Financial Tracker Taskledger v0.4.0 routed-worker comparison

Thread: `01a030bc-c6b0-7ea0-837b-0b283b179064`  
Repository: `/Users/christophercaldwell/Code/business/clients/destinations_beyond/db_financial_tracker`  
Measured window: 2026-08-23T22:27:53Z–2026-08-24T02:37:02Z

## Conclusion

The v0.4.0 two-profile routing worked as designed and is directionally useful.
Sol/xhigh retained specification, routing, and acceptance; Luna/max received
the two routine assignments; Terra/high received the four complex assignments.
The complete thread consumed 104.68M input tokens. That is 22.7% below the
corrected 135.45M total for the earlier Terra/xhigh-orchestrated Financial
Tracker UI thread.

The fairest current estimate is therefore about a **20–25% token reduction for
large UI work**, with low confidence and a plausible range of 0–40%. This is
not proof of a 22.7% causal Taskledger improvement: the old run built much more
scaffolding, while the new run operated on an existing UI and used coarser
tasks. Per current acceptance criterion the new run was 9.7% worse; for the
initial build phase alone it was 23.4% better. Those competing normalizations
are why the estimate should remain directional.

## Measurement correction

This audit found and fixed a benchmark-collector error. Codex full-history
forks copy the parent's historical `token_count` and `context_compacted` events
into the child rollout at startup. The previous collector counted those copied
records as new usage. In the final two-worker wave alone that falsely added
95.40M Sol/xhigh input tokens and four compactions.

Both collectors now identify fork startup history and exclude those copied
events. The complete v0.4 checkpoint excludes 898 inherited token events and
four inherited compaction records. Corrected historical Financial Tracker
checkpoints were regenerated before making this comparison.

## Version and routing verification

- The thread explicitly verified the installed CLI and selected skill as
  Taskledger v0.4.0 before plan approval.
- Ledger assignments contain the required `worker_profile`: two `routine` and
  four `complex`.
- Rollout settings show routine work switching to Luna/max and complex work
  switching to Terra/high. Sol/xhigh remained the root orchestrator/reviewer.
- The profile choices were reasonable. Luna handled typed API/query work and a
  bounded route-completion slice. Visual architecture, shared-table migration,
  and interaction corrections went to Terra.

## Phase reconstruction

Idle gaps are not charged to adjacent phases. The direct drawer repair is kept
separate because it did not create a Taskledger task, although its resulting
commit became the baseline for the final Taskledger queue.

| Phase | Durable work | Current criteria | Submissions / rejected | Changed lines | Input | Output | Compactions |
|---|---:|---:|---:|---:|---:|---:|---:|
| Initial redesign | 3 tasks: 2 routine, 1 complex | 17 | 5 / 2 | 2,202 | 44.34M | 167,404 | 2 |
| EntityTable correction | 1 complex task | 6 | 1 / 0 | 285 | 24.90M | 71,492 | 2 |
| Direct drawer repair | no ledger task | — | — | 117 | 4.09M | 11,484 | 0 |
| Trip interaction corrections | 2 complex tasks | 8 | 2 / 0 | 302 | 31.35M | 77,570 | 1 |
| **Complete thread** | **6 tasks** | **31** | **8 / 2** | **2,906** | **104.68M** | **327,950** | **5** |

The ledger created 36 criterion rows across task revisions; 31 is the sum of
the six tasks' current revisions and is the denominator used here.

## Model attribution

| Cohort | Role | Input | Share | Output | Compactions |
|---|---|---:|---:|---:|---:|
| Sol/xhigh | Root planning, routing, browser acceptance, integration | 63.20M | 60.4% | 140,738 | 3 |
| Terra/high | Four complex assignments | 29.24M | 27.9% | 120,396 | 1 |
| Luna/max | Two routine assignments | 12.24M | 11.7% | 66,816 | 1 |

The root still dominates usage, but much less severely than the earlier
uncorrected measurements implied. Worker implementation accounts for 39.6% of
input, so routing can affect cost, but orchestration and browser review remain
the larger optimization surface.

## Closest historical comparison

The earlier v0.3.0 Terra/xhigh + Luna/max UI thread is the closest available
same-repository comparator. Its corrected total is 135.45M input, not 457.74M.

| View | Earlier UI thread | v0.4 routed thread | Change |
|---|---:|---:|---:|
| Observed complete-thread input | 135.45M | 104.68M | **-22.7%** |
| Input per integrated task | 12.31M | 17.45M | +41.7% |
| Input per current criterion | 3.08M | 3.38M | +9.7% |
| Submission rejection rate | 35.3% (6/17) | 25.0% (2/8) | Improved |

Task and criterion normalization penalizes v0.4 because its tasks were much
coarser. Changed-line normalization is even less trustworthy: the earlier run
created 7,581 foundation/mock-boundary lines in one task, while this run mostly
redesigned existing code.

The initial phases provide a useful sensitivity check. Earlier initial work was
98.78M across 7 tasks and 29 criteria; v0.4 initial work was 44.34M across 3
tasks and 17 criteria. That is 4.7% more input per task but 23.4% less per
criterion. Rejection rates were nearly identical (41.7% versus 40.0%).

## Quality interpretation

Routing improved ledger-level review yield, but the first two project
completions still had user-visible escapes:

- the shared EntityTable had not been adopted across table-shaped detail tabs;
- trip editing and add-on forms still used the wrong presentation pattern;
- several drawers mounted in a way that made transitions jerky;
- the traveler attachment control was native-looking and nonfunctional.

The switch from a trip-creation drawer to `/trips/new` was a user-requested
product change, not a review escape. The final two complex tasks were accepted
without ledger rejection after independent browser checks, but no blind audit
has yet tested the frozen final diff.

## Recommendations

1. Keep Sol as the orchestrator for ambiguous UI and product-intent work.
2. Keep Luna for routine work with explicit boundaries. This run shows it can
   complete a large deterministic follow-on slice when strong shared primitives
   already exist; do not route initial visual architecture to it.
3. Keep Terra for complex implementation. Its four assignments needed one
   rejected submission in the initial architecture task and none in later
   bounded work.
4. Spawn Taskledger workers with `fork_turns: "none"`. The durable worker
   context already carries the assignment, criteria, ownership, and evidence
   contract. Full parent history inflated each worker's live starting context
   even though the copied accounting records themselves were not fresh usage.
5. Start a fresh root task for a new correction specification after a project
   reaches completion. The EntityTable correction cost 24.90M for 285 changed
   lines, showing that accumulated root/browser context remains expensive.
6. Run the predeclared blind Sol audit against `baf3451` before treating quality
   as non-inferior. Record its tokens separately as evaluation cost.
