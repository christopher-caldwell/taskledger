# Taskledger

Taskledger is a local execution ledger for long running coding agent work. A
bounded Task Creator prepares the semantic plan, a foreground Python controller
operates it, workers implement bounded assignments, and independent reviewers
judge immutable submissions. Taskledger records the approved plan, isolated
worktrees, submissions, verification, integration, blockers, and recovery state.

Use it when a change is too large or important to trust to one uninterrupted
chat, but you still want the work and its evidence to remain local to the Git
repository.

Do not automatically reopen Taskledger for every small follow-up. A single
bounded, reversible change with deterministic checks, no parallelism, and no
meaningful architecture or recovery risk is usually cheaper to implement and
review directly. Taskledger begins paying for itself when a specification has
multiple cohesive tasks, cross-layer/high-risk behavior, independent workers,
or real durable-recovery value.

> **Status:** Taskledger 0.6.0 is experimental. The CLI happy path is tested,
> but the full failure-injection and conformance suite is not complete. See
> [Publishing preflight](docs/development/PUBLISHING_CHECKLIST.md) before relying on it for
> critical work.

## What it does

```text
specification
    │
    ▼
bounded Task Creator plans ──► you approve the plan and execution policy
    │
    ▼
Python controller schedules eligible Taskledger assignments
    │
    ├──► routine worker (preferred for fully specified work)
    └──► complex worker (when meaningful judgment remains)
    │
    ▼
bounded reviewer independently judges the exact submitted commit
    │
    ▼
controller applies Taskledger verification and guarded integration
    │
    ▼
bounded final review verifies requirements before project completion
```

Taskledger is the durable authority and safety boundary. Models provide bounded
planning, implementation, and review. They do not operate the project loop.
Python derives mechanical readiness from approved policy and current ledger
state; it does not invent semantic routing or decide that code is correct.

The foreground project controller persists Codex thread and turn identities,
continues ordinary early model stops, enforces dependencies, waves, declared
write surfaces, worker and reviewer capacity, runs independent checks and
review, integrates accepted work, and performs bounded final requirement
review. It pauses when an external result cannot be proven. Live execution
requires explicit opt in. The single assignment supervisor remains available
as the execution primitive below the project controller.

Controller process state lives in separate tables in the project Taskledger
database. Domain changes still use the existing service methods. Worker model
sessions reach worker operations through assignment-scoped app-server dynamic
tools handled by a local broker, so neither worker nor orchestrator credentials
are placed in the model environment and outbound network access stays disabled.

## Requirements

For the standalone CLI:

- macOS or Linux (a POSIX host)
- Python 3.11 or later
- Git 2.20 or later
- a non-bare Git repository with a named branch

For the complete controller workflow, you also need:

- a local Codex surface that supports skills and custom agents;
- the bounded Task Creator and reviewer profiles plus named routine and complex
  worker profiles; and
- permission to create Git worktrees and write `.taskledger/` inside the target
  repository.

Windows and WSL have not been tested. Taskledger has no runtime Python
dependencies outside the standard library.

## Compatibility

| Environment | Ledger CLI | Routed-agent workflow | Support level |
| --- | --- | --- | --- |
| Codex with local shell, skills, and custom agents | Yes | Yes | Primary integration |
| Claude Code | Yes, by invoking the CLI | No adapter included | Manual/experimental |
| Other local coding agents | Yes, by invoking the CLI | No adapter included | Manual/experimental |
| Browser-only or cloud agents without local Git/worktrees | No | No | Unsupported |

The Python CLI and JSON protocol are not tied to a model provider. The included
orchestration package is Codex-specific, however: Codex custom agents use TOML
configuration, while Claude Code uses Markdown subagent definitions and a
different delegation interface. A Claude user can operate the CLI manually, but
this repository does not yet include or test a Claude skill/subagent adapter.

See the official documentation for [Codex skills](https://learn.chatgpt.com/docs/build-skills),
[Codex subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents),
[Claude Code skills](https://code.claude.com/docs/en/slash-commands), and
[Claude Code subagents](https://code.claude.com/docs/en/sub-agents).

## Install

### 1. Install the CLI

Clone the repository, then install with `pip`:

```sh
git clone https://github.com/christopher-caldwell/taskledger.git
cd taskledger
python3 -m pip install .
taskledger --version
```

For development, use `python3 -m pip install -e .` instead.

### 2. Install the Codex skill

Skip this step if you only want the CLI.
The example uses the default Codex home; substitute `$CODEX_HOME` when it is
configured elsewhere.

```sh
mkdir -p ~/.codex/skills/taskledger
rsync -a --delete skills/taskledger/ ~/.codex/skills/taskledger/
```

The repository includes a Codex plugin manifest, but it is not currently
published in a plugin marketplace. Installing the local skill directly is the
supported setup path for now.

### 3. Add the controller profiles to your project

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

The templates use lower strength for routine work, medium strength for complex
work, and high strength for bounded planning and review. Those mappings are
defaults, not Taskledger requirements. Edit `model` and
`model_reasoning_effort` in each file to match the models available to the
consuming repository.

Configure Codex concurrency in the target repository's `.codex/config.toml`:

```toml
[agents]
enabled = true
max_concurrent_threads_per_session = 1
```

Taskledger does not require exact model names. It requires the relative role
strengths and stable profile names. Start a new Codex task after installing or
changing the skill or agent files.

### One-command local refresh

Contributors can install both the CLI and skill from the current checkout with:

```sh
./scripts/install-local.sh
```

This developer convenience script requires `uv` and `rsync`. It force-refreshes
the `uv tool` installation and synchronizes the skill into
`${CODEX_HOME:-$HOME/.codex}/skills/taskledger/`.

## Update

Taskledger has three independently maintained pieces: the CLI, the installed
Codex skill, and the worker profiles copied into each managed repository. From
the Taskledger source checkout, update a `pip` installation with:

```sh
git pull --ff-only
python3 -m pip install --upgrade .
rsync -a --delete skills/taskledger/ ~/.codex/skills/taskledger/
taskledger --version
```

If you installed the CLI with `uv tool`, run `git pull --ff-only` followed by
`./scripts/install-local.sh` instead of installing a second copy with `pip`.
The refresh script does not overwrite project worker profiles because they may
contain local model choices or repository-specific instructions. Compare and
merge updated templates into each repository separately, then start a new Codex
task.

Before an important update, stop active workers and back up the complete
repository-local `.taskledger/` directory. Supported database migrations run
automatically when the new CLI opens a ledger and are forward-only; downgrading
an updated ledger is not supported. See the [complete run guide](docs/RUN_GUIDE.md#update-taskledger)
for profile refresh, backup, verification, restore, and rollback precautions.

## Prepare a repository

Before the first Taskledger run:

1. Make sure the repository has a named branch and a normal Git author identity.
2. Add `.taskledger/` to the repository's `.gitignore` and commit that change if
   necessary.
3. Install the four controller profiles as described above.
4. Put the specification in a file the bounded Task Creator can read.
5. Identify ignored local inputs workers need, such as `.env` files, database
   URLs/services, generated metadata, or certificates. Give the primary a safe
   read-only path or setup command; do not copy credentials into Git or prompts.
6. Start a new Codex task from the repository root.

Taskledger stores its database, scoped worker credentials, and assignment state
under `<repository>/.taskledger/`. Initialization refuses to proceed until that
directory is ignored. Do not commit it or copy its credentials into prompts,
logs, or `.codex` configuration.

Taskledger-created commits inherit the repository's configured Git author and
committer identity. If no identity is configured, the underlying Git operation
fails normally.

## Quick start

Prepare directly from the repository root with a reviewed JSON run configuration:

```sh
taskledger project prepare --input taskledger-run.json
```

The command runs one bounded Task Creator, prints an immutable proposal and its
fingerprint, and stops without starting implementation. Approve that exact
proposal by hash:

```sh
printf '%s\n' '{"preparation_id":"...","approve_proposal_hash":"sha256:...","live":true}' \
  | taskledger project start --input -
```

The Task Creator returns a structured proposal and exits. Python stores that
proposal immutably; only `project start` materializes the hash-approved plan and
execution policy. The foreground Python controller then creates eligible assignments. Workers implement in scoped
worktrees and submit through Taskledger; they do not integrate their own work.
Bounded reviewers inspect exact submissions. The controller validates their
structured verdicts before invoking Taskledger verification and integration.

Task definitions may include exact `required_checks`. Use task-focused checks
for intermediate slices, put strict lint/generated-contract checks on the first
slice that can break them, and reserve the complete cross-project suite for the
integrated final audit. Workers execute each required command through Taskledger
and attach the observed receipt; reported command/exit-code prose is not enough.
The controller reruns each command through the reviewer path against the exact
submitted commit, and worker receipts cannot satisfy that obligation. For a larger new plan,
`plan apply` creates a batch of requirements and tasks transactionally using
short local references, reducing repetitive CLI traffic before `plan validate`.

To resume later, use `taskledger controller resume` with the paused execution
run ID. The ledger persists the approved plan and completed work; no chat
transcript is part of the lifecycle.

Small cohesive assignments may keep one worker across ordered checkpoints by
creating a `lightweight` assignment with checkpoint criteria. Each checkpoint
records an immutable commit and stops until a bounded reviewer approves or rejects that
exact commit. Final submission, independent review, integration, and requirement
verification stay unchanged. Correction packets, checkpoint progression, and
replacement-worker handoff state are returned by `worker context`.

Taskledger can retain intentionally registered evidence outside disposable
worktrees, export provenance-separated evidence deterministically, and wait up
to 60 seconds for selected actionable audit events. The CLI wait preserves a
cursor; the surrounding host still has to wake the model when an event arrives.

For installation variants, routine operation, updates, backup and restore,
troubleshooting, and removal, use the [complete run guide](docs/RUN_GUIDE.md).
Models designing or installing a similar local tool should use the
[AI orchestrated tool installation guide](docs/AI_ORCHESTRATED_TOOL_INSTALLATION.md),
which maps the CLI, skill, plugin, worker profiles, and project state to their
installed locations and verification steps.

When `project complete` succeeds, Taskledger automatically removes each clean,
finalized assignment worktree to reclaim the duplicate checkout and build-output
storage. It retains the assignment branches, commits, integration records, and
ledger history, so the work remains inspectable and revertible through Git.
Dirty, locked, mismatched, or otherwise unsafe worktrees are reported and left
untouched. After preserving or cleaning one, rerun `taskledger project cleanup`
with `{}` on standard input; cleanup is idempotent and available only while the
project remains completed.

## Worker routing

Prefer the `routine` worker when the primary can freeze one approach, name the
owned surfaces, give deterministic acceptance checks, and keep failure local and
reversible. Task size and file count alone do not make work complex.

Routine examples include a cross-file contract rollout with exact types and
checks, repeating an integrated repository/handler pattern across sibling
entities, and generated-client consumer migration when state reconciliation is
split into its own task. Prefer a small complex semantic foundation followed by
routine mechanical rollout tasks over one broad complex migration.

Use the `complex` worker when material judgment still remains—for example:

- architecture or state ownership must be chosen;
- shared contracts or multiple existing implementations must be reconciled;
- product or visual intent needs interpretation;
- security, authorization, data integrity, concurrency, compatibility, or
  destructive behavior is involved; or
- more than one materially different implementation could satisfy the task.

Responsive layout is complex unless exact browser measurements or snapshots
make the result deterministic before implementation. Taskledger automatically
revokes a routine assignment after its second rejected submission and requires
the replacement assignment to use the complex profile.

Routing and parallel safety are separate. Assignments may run together only
when their likely write sets and behavioral assumptions are independent. Shared
configuration, registries, schemas, central exports, entry points, and generated
artifacts need a single owner or a separately integrated foundation task.

Registered specification paths are included in each worker snapshot. They are
frozen authority during implementation and should be changed only by a task that
explicitly owns that exact specification edit. Consolidating maintained-spec
updates into one final documentation reconciliation avoids repeated plan pauses.

After the final implementation integration, the controller dispatches a bounded
high strength reviewer for the integrated cross task audit. Clear implementation
defects go to a fresh bounded Task Creator job, which produces correction tasks.
Product ambiguity pauses for the user. The final reviewer does not remain alive
to operate later work.

When approved behavior replaces an existing active requirement, use
`requirement supersede`. It creates the replacement and retires the prior
requirement atomically while leaving completed task history intact.

## CLI and state

The CLI is intentionally machine-oriented: every command emits one JSON envelope
on standard output. Run commands from the managed repository so Taskledger can
resolve its local state and orchestrator credential.

Useful entry points:

```sh
taskledger --version
taskledger project init --repo /absolute/path/to/repository
taskledger project prepare --input taskledger-run.json
taskledger project start --input approved-preparation.json
taskledger controller run-project --input approved-controller-run.json
printf '{}\n' | taskledger project cleanup --input -
```

The first `project init` call is discovery-only. Confirmed initialization and all
later mutations are normally driven by the Codex skill after approval. The full
command and request-body reference is in
[`skills/taskledger/references/commands.md`](skills/taskledger/references/commands.md).

Set `TASKLEDGER_HOME=/absolute/path` only when intentionally using an external
current-version store. Older shared-home projects are not auto-discovered or
supported.

## Development

Run the test suite from a checkout:

```sh
python3 -m unittest discover -s tests -v
```

## Visual console

The optional local Textual console shows project, task, agent-turn, activity,
usage, and intervention state through the same Taskledger mechanics used by the
CLI. Installing the extra does not change non-visual commands:

```sh
pip install 'taskledger[tui]'
taskledger ui
```

Use `taskledger ui --repo /absolute/path` outside the repository. Opening the
console never starts model work and an uninitialized repository is displayed
without creating ledger files. F1–F6 change sections, Ctrl+P requests a safe
pause for a run owned by this process, and Ctrl+Q exits through the host cleanup
path.

Documentation and design references:

Start with the [documentation index](docs/README.md) for current guidance and historical records.

- [Complete run guide](docs/RUN_GUIDE.md)
- [Installation architecture for AI orchestrated tools](docs/AI_ORCHESTRATED_TOOL_INSTALLATION.md)
- [Product specification](docs/specifications/taskledger-product-spec.md)
- [Technical specification](docs/specifications/taskledger-technical-spec.md)
- [Command registry](docs/reference/COMMAND_REGISTRY.md)
- [Requirement traceability](docs/development/TRACEABILITY.md)
- [Historical implementation and investigation records](docs/archive/README.md)
- [Token-efficiency benchmarks](benchmarks/token-efficiency/README.md)
- [Publishing preflight](docs/development/PUBLISHING_CHECKLIST.md)

## Limitations

- The full failure-injection and 71-requirement conformance suite is incomplete.
- Recovery guarantees should not be trusted for high-risk or irreplaceable work.
- Windows/WSL and non-Codex orchestration adapters are untested or absent.
- The included accounting scripts measure modern host `token_usage_record`
  telemetry with response-ID deduplication and counter reconciliation. These
  token counts are not billing data; missing/inconsistent telemetry is reported
  explicitly, and legacy counters are ignored.
- The plugin is not yet published through a marketplace.

## Support and maintenance

Taskledger is maintained by [Christopher Caldwell](https://github.com/christopher-caldwell).
For setup questions, reproducible bugs, or documentation problems, open a
[GitHub issue](https://github.com/christopher-caldwell/taskledger/issues). Do not
include Taskledger credentials, private source, or provider usage exports in an
issue.

## License

Taskledger is open-source software licensed under the [MIT License](LICENSE).
