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

Current non-live regression evidence: 195 tests passed (11 skipped); the
focused application/controller regression passed 128 tests (1 skipped).

The implementation baseline for the Textual adapter is now TL-UI-1 in
`02-textual-display-spec.md`, pinned to Textual 8.2.8. The first implementation
pass substantially completes Checkpoint A: external TCSS and the built-in dark
theme, a compact Task Ledger header, a single feed observer, six sections,
keyed task/agent/activity/intervention tables, authoritative Overview and Usage
summaries, explicit refresh and help, truthful pause presentation, guarded
active-run exit, and geometry classes including a restricted below-minimum
view. Rendering is slice-aware and task selection is retained by durable ID.

Current UI evidence with the pinned Textual release: the complete non-live
suite passed 199 tests (9 skipped). The focused UI/host suite passed 29 tests,
and a no-Textual run of the UI module passed its static boundary test while
skipping the five Pilot cases. A built wheel was inspected and contains the
external `taskledger.tcss` resource.

Checkpoint A still needs distinct standard-column reduction and a manual run
against the deterministic fake-runtime fixture before it is declared complete.
The following later-checkpoint work remains outstanding:

- task master/detail navigation with stable-ID selection and stale-detail
  generation checks;
- historical activity loading with source-aware cursors, follow mode, scroll
  preservation, and bounded paging;
- exact preparation review and forms for prepare, approval/start, resume,
  answers, blocker resolution, and budget grants;
- complete operation-state presentation, including rejection, failure,
  uncertainty, and pending pause;
- fatal-display shutdown verification through the composition-root safe-stop
  path;
- full wide/standard/compact layout refinement, including responsive table
  columns, keyboard/mouse behavior, focus, modals, and unsent form text;
- Textual Pilot coverage for T01–T24 and UI/headless workflow equivalence.

Those items were part of the supplied specification. They are not required for
headless operation, but they are required before the visual console as a whole
is complete.
