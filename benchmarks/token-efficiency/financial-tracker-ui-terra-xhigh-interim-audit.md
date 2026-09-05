# Interim Terra/xhigh orchestrator audit: Financial Tracker UI

> Measurement correction (2026-08-24): the original 457.74M input estimate
> double-counted token records copied into full-history worker forks. The
> corrected complete-window total is 135.45M: Terra/xhigh 67.72M and Luna/max
> 67.73M, with four actual compactions. Quality findings below remain valid;
> claims that Terra represented 82.7% of usage or that the corrective wave cost
> more than the initial build are superseded by
> `comparison-financial-tracker-v0.4.0-thread-01a030bc.md`.

Thread: `01a02ee8-117a-7561-9e25-d8126582072a`  
Repository: `/Users/christophercaldwell/Code/business/clients/destinations_beyond/db_financial_tracker`  
Measured Taskledger window: 2026-08-23T14:30:00Z–2026-08-23T21:28:00Z  
Status at capture: active; post-completion repairs remain uncommitted

## Interim conclusion

The evidence supports the user's concern with high confidence for this task class: Terra/xhigh was not reliable as the sole orchestrator/reviewer for an ambiguous, reference-driven UI implementation. It repeatedly substituted its own abstraction of “CRM-style patterns” for the explicit instruction to copy the accessible reference implementation, then accepted work with user-visible route and interaction failures.

This is not evidence that Terra/xhigh cannot orchestrate any bounded mechanical task. It is evidence that it should not own product-intent interpretation, task materialization, and final acceptance for high-ambiguity visual/interaction work without a stronger review layer.

## Model and workflow controls

- The UI specification was initially designed by Sol/medium.
- Taskledger planning, orchestration, review, and subsequent repair used Terra/xhigh.
- Implementation workers used Luna/max.
- The injected Taskledger skill was v0.3.0.
- Eleven ledger tasks and 44 criteria were created across two additive plan waves.
- Seventeen submissions were verified: 11 accepted and 6 rejected, a 35.3% rejection rate.
- The ledger reached project completion twice, most recently at `3f9433e1fe9cde19f3966f2a49a5ee908ea00b54`, before the user found additional defects.

## Evidence of orchestrator misunderstanding

1. The original request named the CRM source as the implementation reference for folder structure, pattern matching, data fetching, tables, drawers, filters, and route structure. The Sol-authored specification preserved those references and explicitly selected Mantine, TanStack Table, Bulletproof-style route-local features, and drawers.
2. Terra materialized the first seven tasks without any acceptance criterion requiring exact source-reference parity or browser comparison against the reference. Most criteria emphasized typed data, compilation, and generic accessibility. This converted a key product constraint into non-binding prose.
3. The first completed plan still routed “New trip” to a dead `/new` surface. At 17:06 the user had to restate that creation must open the reference drawer without navigation.
4. At 19:10 the user had to broaden the correction explicitly to every “New” action, the CRM table/filter/search mechanisms, Bulletproof layout, and the existing React Query/OpenAPI flow. Terra created four corrective Taskledger tasks.
5. The corrective quality task was accepted with notes claiming browser smoke coverage for trip/client/reference drawers and filters. After durable project completion, the user found that trip navigation changed the URL without changing the view and that drawers appeared abruptly rather than using the reference transition.
6. Terra then diagnosed that detail routes had been nested beneath list routes that render no outlet. This was a basic navigation defect that should have been found during the accepted browser review.
7. Terra explicitly admitted the implementation had stopped at “superficial structural similarity.” After another instruction to copy the source exactly, it replaced the custom drawer approximation with the actual Mantine pattern.
8. It then introduced reference-project naming into the target application, prompting another user correction. Terra acknowledged misreading “copy the CRM” as permission to carry over CRM labels rather than copying structure and behavior while retaining Financial Tracker terminology.

## Attribution

### Terra orchestrator/reviewer

- Failed to preserve the most important qualitative constraint when converting the specification into ledger criteria.
- Narrowly corrected the first reported symptom instead of generalizing the user's already-explicit “all New actions use this reference pattern” intent.
- Accepted static/source assertions and incomplete browser checks as evidence of behavior.
- Failed to detect a broken detail-route hierarchy and visible drawer-transition mismatch before completion.
- Repeatedly required the user to translate “copy the reference” into progressively more literal instructions.

### Luna workers

- Produced the route, interaction, and reference-fidelity defects.
- One worker violated its isolated-worktree boundary and wrote into the canonical worktree, which Terra correctly detected and contained.
- Worker mistakes mattered, but Taskledger's architecture assigns final intent interpretation and acceptance to the orchestrator; they do not explain the accepted escapes.

### Taskledger

- Correctly blocked integration on the dirty canonical worktree.
- Preserved submissions, rejections, corrections, integrations, and the second corrective plan.
- Did not cause the semantic misunderstanding. The ledger faithfully enforced the incomplete acceptance criteria that Terra wrote.
- The run demonstrates that durable execution cannot compensate for an orchestrator that materializes the wrong interpretation of product intent.

## Interim usage

| Cohort | Input tokens | Output tokens | Compactions |
|---|---:|---:|---:|
| Terra/xhigh | 67.72M | 173,094 | 4 |
| Luna/max | 67.73M | 404,727 | 0 |
| **Total** | **135.45M** | **577,821** | **4** |

Terra/xhigh accounts for 50.0% of corrected observed input. The orchestrator
and eleven Luna worker rollouts contributed almost exactly equal input.

| Phase | Input tokens | Compactions | Durable work |
|---|---:|---:|---|
| Initial Taskledger build | 98.78M | 2 | 7 tasks, 29 criteria, 11,854 changed lines |
| CRM-alignment corrective wave | 22.87M | 1 | 4 tasks, 15 criteria, 1,761 changed lines |
| Post-completion direct repair through capture | 12.45M | 1 | Active, uncommitted |

The corrective wave was far smaller than the initial build after inherited fork
history is excluded. It still represents avoidable rework because the user had
to report defects after accepted browser evidence and durable completion. The
quality tipping-point conclusion remains supported; the original token-based
claim does not.

Task execution windows overlap because several assignments ran in parallel; their token totals must not be summed. Phase totals above use non-overlapping wall-clock windows.

## Recommendation

1. Do not use this thread as a clean v0.3 token-efficiency sample or as evidence for the planned Terra/Luna quality experiment; it changed model mix, underwent two corrective plans, and remains active.
2. Use Sol/high as the orchestrator/reviewer for ambiguous UI, product-design, reference-fidelity, architecture, and acceptance work.
3. Reserve Terra for bounded mechanical orchestration or secondary review where requirements are already executable and visually objective.
4. If Luna remains the implementation worker, require screenshot/browser assertions and exact reference-component reuse in each affected task criterion—not only in a final hardening task.
5. Make reference fidelity testable: named source components, required library primitives, route behavior, screenshots at defined viewport states, animation duration/lifecycle, and target-project terminology.
6. After the active thread stabilizes, capture a final checkpoint and perform the planned blind Sol/high audit. Treat every issue found after either project completion as a review escape.
