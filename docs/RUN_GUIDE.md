# Taskledger run guide

This guide covers the complete local lifecycle: installation, repository setup,
normal operation, updates, backup and restore, troubleshooting, and removal.
Taskledger is currently experimental. Its happy path is covered by the acceptance
suite, but its full failure-injection and conformance suite is not complete. Do
not rely on interrupted-operation recovery as the only safeguard for
irreplaceable work.

## Components and state

A complete Codex installation has three independently maintained components:

1. The `taskledger` Python CLI, which owns the ledger protocol and Git
   operations.
2. The Taskledger Codex skill, which guides the bounded Task Creator and human
   approval phase.
3. Task Creator, reviewer, routine worker, and complex worker profiles in every
   repository that will use the controller.

The default project state is stored in `<repository>/.taskledger/`. That
directory contains the SQLite database, orchestrator and worker credentials,
assignment metadata, retained evidence/check output, and deterministic exports.
It must be ignored by Git and must not be copied into prompts, logs, issue
reports, or source control.

An explicit `TASKLEDGER_HOME=/absolute/path` may select an intentionally external
current-version store. Taskledger does not discover or support older shared-home
stores; each managed project must use the current protocol and schema.

## Requirements

For the standalone CLI:

- macOS or Linux on a POSIX host;
- Python 3.11 or later;
- Git 2.20 or later; and
- a non-bare Git repository with a symbolic branch.

For the Codex controller workflow, you also need a local Codex surface with
skills and custom agents, permission to create Git worktrees, and suitable
models for bounded planning, review, routine work, and complex work. Windows and
WSL are not currently tested.

## Install from a source checkout

Taskledger is not currently published to PyPI or a Codex plugin marketplace.
Install it from a local clone:

```sh
git clone https://github.com/christopher-caldwell/taskledger.git
cd taskledger
python3 -m pip install .
taskledger --version
```

If the `taskledger` executable is not found after installation, make sure the
Python environment's scripts directory is on `PATH`. You can still verify the
package with:

```sh
python3 -m taskledger --version
```

For Taskledger development, install the checkout in editable mode:

```sh
python3 -m pip install -e .
```

### Install the Codex skill

Skip this section if you only need the machine-oriented CLI.

The following uses the default Codex home. If `CODEX_HOME` points elsewhere,
replace `~/.codex` with that directory.

```sh
mkdir -p ~/.codex/skills/taskledger
rsync -a --delete skills/taskledger/ ~/.codex/skills/taskledger/
```

Start a new Codex task after installing or changing the skill. Existing tasks
may continue using the skill snapshot with which they started.

### Install the worker profiles in a managed repository

From the repository that Taskledger will manage:

```sh
mkdir -p .codex/agents
cp ~/.codex/skills/taskledger/assets/taskledger-worker-routine.toml \
  .codex/agents/taskledger-worker-routine.toml
cp ~/.codex/skills/taskledger/assets/taskledger-worker-complex.toml \
  .codex/agents/taskledger-worker-complex.toml
cp ~/.codex/skills/taskledger/assets/taskledger-reviewer.toml \
  .codex/agents/taskledger-reviewer.toml
cp ~/.codex/skills/taskledger/assets/taskledger-task-creator.toml \
  .codex/agents/taskledger-task-creator.toml
```

The templates use Luna for routine work, Terra for complex work, and Astra for
bounded planning and review. These are defaults, not protocol requirements.
Edit `model` and `model_reasoning_effort` to models available in the consuming
repository while preserving the profile names and Taskledger instructions.

Configure Codex concurrency in the managed repository's `.codex/config.toml`.
For example:

```toml
[agents]
enabled = true
max_concurrent_threads_per_session = 1
```

The Task Creator makes semantic routing and concurrency decisions once. The
Python controller enforces the approved policy with bounded runtime capacity.

### Contributor convenience install

From a Taskledger checkout, this command force-refreshes a `uv tool`
installation and synchronizes the skill into
`${CODEX_HOME:-$HOME/.codex}/skills/taskledger/`:

```sh
./scripts/install-local.sh
```

It requires `uv` and `rsync`. It does not install or overwrite worker profiles
in managed repositories; update those separately as described below.

## Quick start

### 1. Prepare the managed repository

From the repository root:

```sh
printf '\n.taskledger/\n' >> .gitignore
git add .gitignore
git commit -m "Ignore Taskledger local state"
```

If `.taskledger/` is already ignored, do not add a duplicate entry or commit.
Also confirm that the repository has a symbolic branch, a usable Git author
identity, the four controller profiles, and a written specification that Codex can
read. Identify any ignored inputs that worker checks need—such as `.env` files,
local services, certificates, or generated metadata—and provide a safe setup
command or read-only location. Never place credentials in Git or prompts.

After recording these declarations, the primary runs `project preflight`.
It checks versions, repository state, profile configuration, declared file
metadata, and explicitly named TCP services without reading input contents.
A profile TOML proves configuration only; the host must separately report
whether it can launch the exact named profile with its configured model/effort.
On macOS, declare the actual service host and port consumed by the check (for
example, a Compose-published PostgreSQL port), rather than assuming a Linux
Docker socket path is a required input.

### 2. Prepare and start through the CLI

Create a run configuration naming the specification, limits, and declared local
inputs/services, then run:

```sh
taskledger project prepare --input taskledger-run.json
```

Review the returned requirements, tasks, routing, waves, assumptions, and
proposal fingerprint. Start only the exact immutable proposal:

```sh
printf '%s\n' '{"preparation_id":"...","approve_proposal_hash":"sha256:...","live":true}' \
  | taskledger project start --input -
```

`project prepare` initializes Taskledger when needed, runs and accounts for the
bounded Task Creator, then stops. `project start` rechecks repository, spec,
profiles, configuration, proposal hash, and preflight before transactionally
materializing the plan and starting the foreground controller. It creates eligible
assignments, continues unfinished worker turns, dispatches bounded independent
reviewers, and invokes Taskledger verification and integration.

Preparation selects one registered specification revision after synchronization
and sends those stored bytes, with their hash, to the Task Creator. A filesystem
change during planning or before approval is rejected instead of silently
rebinding the proposal. Sources are likewise canonicalized before approval: one
requirement may cite one specification locator once, with distinct excerpts
preserved in a single entry separated by blank lines. An older approved proposal
that is not already in that canonical form must be prepared again.

Each preparation response includes a `run_group_id`. Reuse it only when retrying
the same trial. Execution reports keep the selected preparation as a direct
subtotal and freeze the group attempts that led to it, so a later retry cannot
change historical cost. A preflight failure that did not call a model is shown as
a zero-model attempt; missing or unresolved model usage remains incomplete.

Unexpected CLI failures include a correlation ID and phase. With `--verbose`,
the CLI also prints the owner-only diagnostic path under `.taskledger/diagnostics/`.
Those records contain exception class and stack-frame metadata, not request
bodies, local variables, or credentials.

### Detached foreground launch

The controller intentionally remains a foreground process. To keep it running
after terminal detachment without retrying a terminal failure, start exactly one
process in tmux:

```sh
tmux new-session -d -s taskledger-run 'exec taskledger project start --input approved-preparation.json'
tmux list-panes -t taskledger-run -F '#{pane_pid}'
```

The listed pane PID is the controller because `exec` replaces the shell. Attach
with `tmux attach -t taskledger-run`. Request a durable safe pause with
`kill -INT <pane-pid>`; do not wrap the command in a restart loop. The controller
records the pause and can later resume through its normal CLI path.

### 3. Resume an existing project

Resume the paused foreground execution run directly:

```sh
printf '%s\n' '{"run_id":"...","live":true}' | taskledger controller resume --input -
```

Taskledger loads its durable plan and history from `.taskledger/`; the previous
chat transcript is not the source of truth. A routine refresh uses
`project resume`. A full `project recover` is needed only for a new or lost
baseline, when resume reports `full_recovery_required`, or when an interrupted
operation must be reconciled.

### 4. Complete and clean up

After approved implementation work is integrated, the controller runs a bounded
final review, records requirement verification through Taskledger, and invokes
`project complete` only when existing completion gates pass. Completion records
the canonical commit and removes clean, finalized assignment worktrees.
Assignment branches, commits, integration records, and ledger history remain.

Dirty, locked, mismatched, or otherwise unsafe worktrees are left untouched.
After preserving or cleaning them, ask Codex to retry `project cleanup`.

## Normal operating model

Use Taskledger for multi-task specifications, cross-layer or high-risk work,
independent workers, or work that benefits materially from durable recovery.
A small, bounded, reversible change with deterministic checks and no meaningful
architecture or recovery risk is usually better handled directly.

The normal lifecycle is:

1. Discover or load the repository's ledger.
2. Inspect the specification and Git state without mutation.
3. Approve the plan.
4. Approve initialization, materialization, and the complete execution policy.
5. Route fully specified work to the routine profile and judgment-heavy work to
   the complex profile.
6. Let each worker progress through declared vertical checkpoints and submit
   from its scoped assignment worktree.
7. Let the Python controller run reviewer checks, dispatch bounded independent
   reviewers, validate structured verdicts, and invoke verification and
   integration.
8. Let the controller dispatch the bounded integrated final review, verify
   requirements, and complete the project.

Do not manually move worker commits, delete assignment worktrees, resolve
Taskledger merge conflicts, rotate credentials, or edit the database while a
controller run is active. Let the controller and existing Taskledger service
methods record every state transition.

### Lightweight checkpoint flow

Use a lightweight assignment when one worker can coherently own a small project
through several early end-to-end slices. Declare ordered checkpoint labels and
criteria at assignment creation. The worker implements one slice, calls
`worker checkpoint`, and stops. The primary loads `checkpoint review-context`,
reviews the exact commit, and records `checkpoint verify`. Approval unlocks the
next slice; rejection returns exact corrections through `worker context`.

Checkpoint approval never accepts a task or integrates code. A continued task
or specification revision supersedes prior checkpoint approvals. A replacement
worker can resume the same assignment after token rotation by loading the
bounded durable context packet. Use separate isolated assignments when
concurrent ownership provides value.

### Receipts, retained artifacts, and export

Workers run exact declared required checks with `worker check` and attach receipt
IDs to their final submission. Taskledger records command, cwd, source commit,
tree fingerprints, timing, exit status, and bounded retained output. The primary
runs the same declared checks with `submission check` at the exact submitted
commit. Worker receipts never satisfy reviewer obligations. A command that
changes source is marked stale and must be rerun from a clean current state.

Register intentional individual evidence files only. Symlinks, traversal,
directories, and oversized files are rejected. Retained artifacts live outside
assignment worktrees and survive completion cleanup. `evidence export` writes
deterministic JSON that keeps source-use claims, worker claims, observed
executions, and reviewer conclusions distinct; it generates no semantic verdict.

### Event-driven host loop

`project resume` returns `latest_event_sequence`. Pass it to `project wait` with
an explicit event filter and a timeout of at most 60 seconds. Reuse the cursor
after timeout and persist the returned cursor after events. Future cursors are
rejected. The response reports the retained event floor and states that durable
audit pruning is not configured, so `missed_events` remains false in this version.
The CLI blocks outside model reasoning, but the host still has to run the wait
and wake the model. Bounded waits preserve user interruption and progress updates.

For exact CLI request bodies, authorization, gates, and exit codes, see the
[command reference](../skills/taskledger/references/commands.md).

## Update Taskledger

Updates have three parts: CLI, skill, and worker profiles. Updating only one can
leave the installed workflow inconsistent.

Before updating, finish or pause active agents and make a protected backup of
every important `.taskledger/` directory. The CLI applies supported schema
migrations automatically when it next opens a ledger. Those migrations are
forward-only; downgrading an updated ledger is not supported.

### Update a pip installation

In a clean Taskledger source checkout:

```sh
git pull --ff-only
python3 -m pip install --upgrade .
taskledger --version
```

If the checkout has local changes, preserve or commit them and reconcile the
upstream update deliberately. Do not discard local work merely to run these
commands.

For an editable installation, pull the checkout and rerun
`python3 -m pip install -e .` so packaging or entry-point changes are applied.

### Update a uv tool installation and the skill

In the Taskledger source checkout:

```sh
git pull --ff-only
./scripts/install-local.sh
```

If the CLI was installed with `pip`, update it with `pip` rather than creating a
second `uv tool` installation. You can update only the skill with:

```sh
rsync -a --delete skills/taskledger/ \
  "${CODEX_HOME:-$HOME/.codex}/skills/taskledger/"
```

Start a new Codex task after a skill update.

### Update worker profiles safely

Worker profiles are copied into each managed repository and may contain local
model choices or repository-specific instructions. The refresh script therefore
does not overwrite them.

For every managed repository, compare its profiles with the new templates:

```sh
diff -u ~/.codex/skills/taskledger/assets/taskledger-worker-routine.toml \
  .codex/agents/taskledger-worker-routine.toml
diff -u ~/.codex/skills/taskledger/assets/taskledger-worker-complex.toml \
  .codex/agents/taskledger-worker-complex.toml
```

Merge Taskledger instruction changes while preserving deliberate local model,
reasoning-effort, and repository-specific settings. Start a new Codex task after
updating the profiles.

### Verify an update

```sh
taskledger --version
python3 -m taskledger --version
```

Both should report the intended version and resolve to the same Python
environment. In an existing managed repository, ask a new Codex task to resume
the project. Confirm that it loads the existing ledger instead of proposing a
new initialization. If opening the ledger fails after an update, preserve the
current state and follow the restore and troubleshooting sections; do not
initialize a replacement ledger.

## Back up and restore ledger state

Ledger backups contain credentials and private project history. Store them with
owner-only access and never commit or upload them with a bug report.

### Back up

Stop Taskledger commands and active workers for that repository. Copy the entire
`.taskledger/` directory, not only `taskledger.sqlite3`, because SQLite uses WAL
sidecar files and Taskledger stores credentials beside the database:

```sh
cp -a /absolute/path/to/repository/.taskledger \
  /private/backup/location/taskledger-backup
```

For an external current-version store, back up the entire configured
`TASKLEDGER_HOME` instead.
Keep normal Git backups as well: the ledger records coordination and evidence,
while Git owns the implementation commits and assignment branches.

### Restore

Stop active Taskledger and Codex worker processes. Preserve the current
`.taskledger/` directory under a different private name, then copy the complete
backup back to `<repository>/.taskledger/`. Restore it only to the same
repository and Git history from which it was captured. Ensure the directory and
credential files remain accessible only to the owner.

After restoration, open a new Codex task in the repository and ask it to use
Taskledger to load the project. If Git or ledger state has diverged, do not force
an outcome or initialize again; let the primary inspect the recovery document
and repository facts.

### Version policy

There is no database downgrade or older-client compatibility path. Keep the
CLI, skill, worker profiles, and project schema on the current version. Preserve
a complete private backup before upgrading and never rewrite the database by
hand.

## Troubleshooting

### The command is not found

Confirm which installer and Python environment you used. Try
`python3 -m taskledger --version`, inspect the environment's scripts directory,
and avoid installing a second copy through another package manager. Reinstall
with the same method if necessary.

### Codex does not discover the skill or workers

Confirm the skill exists at
`${CODEX_HOME:-$HOME/.codex}/skills/taskledger/SKILL.md` and both worker TOML
files exist in the managed repository's `.codex/agents/` directory. Validate
that their configured models are available. Then start a new Codex task; an
already-running task may retain its earlier skill and agent configuration.

### Initialization says `.taskledger/` is not ignored

Add `.taskledger/` to the managed repository's ignore rules and confirm it with:

```sh
git check-ignore -v .taskledger/
```

Commit the ignore-rule change when appropriate, then repeat discovery. Do not
force initialization or commit the ledger directory.

### Taskledger cannot find the project

Run it from inside the managed repository. If the project uses an intentionally
external current-version store, set its exact configured `TASKLEDGER_HOME`. Do
not use the override to bypass inaccessible state or create a duplicate project.

### The canonical repository is dirty or on the wrong branch

Preserve and resolve the user's changes intentionally. Do not automatically
stash, reset, switch branches, or discard files on Taskledger's behalf. Resume
only after the repository is on its recorded canonical branch and the primary
has confirmed a safe clean state for integration.

### A worker model is unavailable

Edit the corresponding project worker profile to use an available model while
keeping the stable profile name and instructions. Start a new Codex task so the
new configuration is loaded. Do not silently substitute routine and complex
profiles for one another during an active assignment.

### Resume requests full recovery or the CLI exits with code 6

Ask the primary to load `project recover` and follow the supplied recovery
document. `operation adopt-success` and `operation mark-failed` are allowed only
when the recorded operation and current Git OID provide the required proof. Do
not resolve merge conflicts, move commits, delete worktrees, or force an
operation outcome manually.

### Ledger storage is unavailable

Check owner permissions and available disk space for the complete ledger
directory. For an inaccessible external current-version store, grant access to
its configured directory and retry with the same explicit `TASKLEDGER_HOME`.
Storage failure alone does not mean recovery is required, and it is not
permission to initialize a replacement ledger.

### Completion leaves worktrees behind

Taskledger intentionally skips a worktree that is dirty, locked, on an
unexpected branch, in the middle of a Git operation, or otherwise unsafe.
Preserve or clean it deliberately, then ask Codex to retry `project cleanup`.
Never force-delete it merely to make cleanup pass.

### Reading CLI failures

Every CLI call returns one JSON envelope. Exit code `2` is an invalid request,
`3` a state or storage gate, `4` an authorization failure, `5` a known-safe Git
failure, `6` an interrupted operation needing reconciliation, and `70` an
unexpected internal error. Only exit code `6` by itself implies recovery work.
Use the returned error code, details, and allowed actions rather than guessing
an unlisted command or flag.

## Remove Taskledger

Finish or intentionally abandon active work and make any desired backups first.
Uninstall the CLI with the same installer that installed it:

```sh
python3 -m pip uninstall taskledger
```

or:

```sh
uv tool uninstall taskledger
```

To remove the Codex integration, delete the exact
`${CODEX_HOME:-$HOME/.codex}/skills/taskledger/` directory and the two exact
Taskledger worker TOML files from each managed repository. Review local changes
before deleting profiles because they may contain repository-specific settings.
Start a new Codex task after removal.

Uninstalling the CLI or skill does not delete `.taskledger/`, assignment
branches, commits, or worktrees. Retaining `.taskledger/` allows a compatible
future installation to resume the project. Deleting it permanently removes the
ledger, credentials, plan, audit history, and recovery metadata; Taskledger has
no undo or supported `uninit` command. Remove it manually only after confirming
that no active work needs it and that any required backup is usable.

## Further reference

- [Command and request-body reference](../skills/taskledger/references/commands.md)
- [Product specification](../taskledger-product-spec.md)
- [Technical specification](../taskledger-technical-spec.md)
- [Requirement traceability](../TRACEABILITY.md)
- [Publishing preflight](../PUBLISHING_CHECKLIST.md)
