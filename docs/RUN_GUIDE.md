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
2. The Taskledger Codex skill, which tells the primary agent how to plan,
   approve, route, review, and recover work.
3. Two custom worker profiles in every repository that will use routed workers.

The default project state is stored in `<repository>/.taskledger/`. That
directory contains the SQLite database, orchestrator and worker credentials,
and assignment metadata. It must be ignored by Git and must not be copied into
prompts, logs, issue reports, or source control.

Taskledger 0.1 projects may instead use the legacy shared
`~/.taskledger/` directory. Current releases try to discover that store. Use an
explicit `TASKLEDGER_HOME=~/.taskledger` only for an existing legacy project;
do not use it to create a second ledger for a repository.

## Requirements

For the standalone CLI:

- macOS or Linux on a POSIX host;
- Python 3.11 or later;
- Git 2.20 or later; and
- a non-bare Git repository with a symbolic branch.

For the routed Codex workflow, you also need a local Codex surface with skills
and custom agents, permission to create Git worktrees, and suitable models for
the primary, routine worker, and complex worker roles. Windows and WSL are not
currently tested.

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
```

The templates use Luna for routine work and Terra for complex work. These are
defaults, not protocol requirements. Edit `model` and
`model_reasoning_effort` to models available in the consuming repository while
preserving the profile names and Taskledger-specific instructions.

Configure the primary model and assignment concurrency in the managed
repository's `.codex/config.toml`. For example:

```toml
model = "gpt-5.6-sol"
model_reasoning_effort = "medium"

[agents]
enabled = true
max_concurrent_threads_per_session = 1
```

The primary must be capable of decomposition and independent review. Increase
concurrency only when the primary has confirmed that the assignments' likely
write sets and behavioral assumptions are independent.

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
identity, the two worker profiles, and a written specification that Codex can
read. Identify any ignored inputs that worker checks need—such as `.env` files,
local services, certificates, or generated metadata—and provide a safe setup
command or read-only location. Never place credentials in Git or prompts.

### 2. Start the workflow through Codex

Open a new Codex task from the managed repository root and ask:

```text
Use $taskledger to implement docs/my-spec.md. Inspect the repository, propose
the plan, and stop for the required approvals before making changes.
```

The first `project init` invocation is discovery-only. For a new or materially
revised plan, the skill stops at two gates:

1. Plan approval covers the outcome, observable success, scope, task boundaries,
   dependencies, assumptions, and possible parallel waves.
2. Execution approval covers the exact branch and Git state, initialization and
   commit actions, validated ledger plan, worktree prerequisites, and the first
   assignment wave.

The primary creates only the approved assignments. Workers implement in
isolated worktrees and submit through Taskledger. The primary independently
reviews and tests the exact submitted commit before recording verification and
integration.

### 3. Resume an existing project

Open a new Codex task in the managed repository and ask:

```text
Use $taskledger to resume this repository's existing project and show me the
next safe action.
```

Taskledger loads its durable plan and history from `.taskledger/`; the previous
chat transcript is not the source of truth. A routine refresh uses
`project resume`. A full `project recover` is needed only for a new or lost
baseline, when resume reports `full_recovery_required`, or when an interrupted
operation must be reconciled.

### 4. Complete and clean up

After every active requirement is verified and no task, review, blocker, or
uncertain operation remains, the primary runs `project complete`. Completion
records the canonical commit and removes clean, finalized assignment worktrees.
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
4. Approve initialization, materialization, and the first assignment wave.
5. Route fully specified work to the routine profile and judgment-heavy work to
   the complex profile.
6. Let each worker submit from its isolated assignment worktree.
7. Have the primary independently inspect, test, verify, and integrate each
   submission.
8. Have the primary run the integrated final audit, verify requirements, and
   complete the project.

Do not manually move worker commits, delete assignment worktrees, resolve
Taskledger merge conflicts, rotate credentials, or edit the database while an
agent-run workflow is active. Let the primary follow the skill and command
reference so that every state transition remains recorded.

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

For a legacy project, back up the entire configured `TASKLEDGER_HOME` instead.
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

### Roll back an update

There is no in-place database downgrade. To return to an older Taskledger
version after a newer CLI has opened and migrated a ledger, stop all related
processes, preserve the newer ledger, restore the complete pre-update backup,
and reinstall the exact older CLI, skill, and worker-profile revisions that
created it. The restored ledger must be paired with the same repository and
compatible Git history. If you do not have that complete backup and versioned
source, keep the newer version and diagnose the problem without rewriting the
database manually.

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

Run it from inside the managed repository. If the project was created by
Taskledger 0.1 in the legacy shared store, retry only that existing project with
`TASKLEDGER_HOME=~/.taskledger`. Do not use the override to bypass inaccessible
state or create a duplicate project.

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
directory. For an inaccessible legacy store, grant access to the existing
`~/.taskledger` directory and retry with the same explicit `TASKLEDGER_HOME`.
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
