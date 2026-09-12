# Task Ledger visual console implementation status

The headless application and lifecycle layer in
`01-headless-mechanics-spec.md` is implemented. Its public contract can prepare,
approve and start, resume project or assignment runs, request safe pause, apply
human interventions, query immutable snapshots/details/history/reports, and
observe committed changes without Textual installed. See
`HEADLESS_ACCEPTANCE.md` for the M01–M24 mapping, commands, measurements, and the
unavailable Linux coverage declaration.

The remaining work is the Textual adapter described by
`02-textual-display-spec.md`. The existing adapter is a display shell; the
following specified work is still outstanding:

- task master/detail navigation with stable-ID selection and stale-detail
  generation checks;
- historical activity loading with source-aware cursors, follow mode, scroll
  preservation, and bounded paging;
- exact preparation review and forms for prepare, approval/start, resume,
  answers, blocker resolution, and budget grants;
- complete operation-state presentation, including rejection, failure,
  uncertainty, and pending pause;
- guarded exit and fatal-display shutdown through the headless safe-stop path;
- the wide, compact, and below-minimum layouts, including resize state
  preservation, keyboard/mouse behavior, focus, modals, and unsent form text;
- Textual Pilot coverage for T01–T24 and UI/headless workflow equivalence.

Those items were part of the supplied specification. They are not required for
headless operation, but they are required before the visual console as a whole
is complete.
