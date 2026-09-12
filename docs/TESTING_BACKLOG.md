# Testing backlog

This page carries unresolved work forward from the archived original
implementation checklist. Moving that checklist did not complete Slice 11.

- [ ] Complete or explicitly defer exhaustive failure injection and the full
  original 71 requirement conformance gate. Use [traceability](development/TRACEABILITY.md)
  to identify evidence and gaps; do not infer completion from broad test counts.
- [ ] Resolve the planned and implemented but unproven controller cases in the
  [test plan](CONTROLLER_TEST_PLAN.md) and [coverage ledger](CONTROLLER_TEST_COVERAGE.md).
  Preserve the separate status of deferred live experiments.
- [ ] Map T01–T24 in the [Textual specification](taskledger-ui-architecture/02-textual-display-spec.md)
  to attributable checks and record any missing checks. Aggregate UI test counts
  are not a complete acceptance matrix.
- [ ] Record supported platform and clean installation evidence required by the
  [publishing checklist](development/PUBLISHING_CHECKLIST.md).

Keep new evidence tied to its command, source commit, environment, and result.
Historical runs retain their original outcomes and limitations. Paid live
experiments require their normal explicit opt in.

For documentation changes, run `python3 scripts/check_docs.py`. It checks local
Markdown link destinations and the UI package manifest hashes without network
access; it does not validate anchors, external links, or example commands.
