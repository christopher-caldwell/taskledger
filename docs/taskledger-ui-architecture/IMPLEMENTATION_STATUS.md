# Task Ledger visual console implementation status

The headless application and lifecycle layer in
`01-headless-mechanics-spec.md` is implemented and accepted. Its public contract can prepare,
approve and start, resume project or assignment runs, request safe pause, apply
human interventions, query immutable snapshots/details/history/reports, and
observe committed changes without Textual installed. See
`HEADLESS_ACCEPTANCE.md` for the M01–M24 mapping, commands, and measurements.
The correctness hardening pass added
broker-to-host committed-change propagation (including isolated worker SQLite
connections), OPEN/CLOSING/CLOSED host shutdown semantics, atomic service
adoption rollback, safe application error normalization, engine-owned report
construction, current durable project-state projection, and removal of
duplicated CLI lifecycle code.

M04, M05, M14, M17, and M24 now have deterministic acceptance fixtures and are
recorded as Pass. The coverage includes observer equivalence; real
broker-originated question, blocker, and submit updates; reviewer and
integration pause boundaries; fatal primary-client cleanup of an owned active
lifecycle; and one successful public-contract-only prepare-to-completion run.

Earlier headless implementation evidence, retained from before the Textual
workflow pass: 195 tests passed (11 skipped); the focused application/controller
regression passed 128 tests (1 skipped). The original record does not identify
an execution timestamp. These are historical run totals.

The Textual adapter implements the functional TL-UI-1 operator console and is
pinned to Textual 8.2.8. It uses external TCSS, one feed observer, immutable
revision-ordered snapshots, slice-aware rendering, six keyboard-accessible
sections, and guarded lifecycle shutdown. The visual layer uses only the public
application/client contract and remains optional for headless installations.

The console now supports the complete normal operator workflow: prepare with
validated limits and preflight JSON; inspect an exact immutable preparation;
approve and start its exact proposal hash; observe workers, reviewers, current
activity, interventions, and usage; inspect task evidence/details; page tasks
and durable source-aware activity history; answer questions; resolve blockers;
grant budget; request safe pause; resume; inspect completion and an immutable
run report; and exit without abandoning an owned lifecycle. Accepted lifecycle
operations are presented as admissions, then reconciled once through
`operation_status` after their active snapshot entry disappears.

Wide terminals provide task master/detail behavior. Standard and compact
terminals use full detail screens, and the restricted below-minimum screen is
retained. Detail requests are identity/generation guarded, forms survive
unrelated snapshots, untrusted values use literal-safe rendering, and history
preserves Audit/Controller source identity.

Later Textual workflow evidence recorded in the repository by 2026-09-12
with Textual 8.2.8: 18 focused UI tests pass, including the
specified 140x40, 100x30, 80x24, 70x22, and 55x18 geometries. The complete
non-live regression passes 211 tests (9 skipped).

These recorded runs have not been repeated as part of documentation cleanup.
Full T01–T24 acceptance is not established by these aggregate totals; keep the
remaining case mapping and checks in the [testing backlog](../TESTING_BACKLOG.md).

## Known limitations / future polish

- Activity follow behavior is intentionally basic: Recent always shows the
  latest bounded snapshot and History uses an explicit Load more action.
- Standard/compact tables rely on Textual horizontal scrolling rather than a
  fully dynamic column-removal system.
- The preparation, task, and run-report renderers are utilitarian generic
  structured views rather than highly specialized visualizations.
- Keyboard operation is more refined than mouse-first operation, and compact
  spacing can be polished further.
- Provider transcripts and a browser UI remain out of scope.
