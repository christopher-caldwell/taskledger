# Taskledger

Taskledger is a local execution ledger for long-running coding-agent work. A
strong primary agent plans, routes, and reviews the work; lower-cost workers
implement bounded assignments; Taskledger records the approved plan, isolated
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

> **Status:** Taskledger 0.5.2 is experimental. The CLI happy path is tested,
> but the full failure-injection and conformance suite is not complete. See
> [Publishing preflight](PUBLISHING_CHECKLIST.md) before relying on it for
> critical work.

## What it does

```text
specification
    │
    ▼
primary agent plans ──► you approve the plan and execution wave
    │
    ▼
Taskledger creates scoped assignments and isolated Git worktrees
    │
    ├──► routine worker (preferred for fully specified work)
    └──► complex worker (when meaningful judgment remains)
    │
    ▼
primary agent independently reviews the exact submitted commit
    │
    ▼
Taskledger records verification, integration, and requirement completion
```

Taskledger is the durable record and safety boundary. It does **not** generate a
plan, choose a model, decide whether code is correct, or replace the primary
agent's review.

## Requirements

For the standalone CLI:

- macOS or Linux (a POSIX host)
- Python 3.11 or later
- Git 2.20 or later
- a non-bare Git repository with a named branch

For the complete routed-agent workflow, you also need:

- a local Codex surface that supports skills and custom agents;
- one primary/orchestrator model and two named worker profiles; and
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

### 3. Add the two worker profiles to your project

From the repository that Taskledger will manage:

```sh
mkdir -p .codex/agents
cp ~/.codex/skills/taskledger/assets/taskledger-worker-routine.toml \
  .codex/agents/taskledger-worker-routine.toml
cp ~/.codex/skills/taskledger/assets/taskledger-worker-complex.toml \
  .codex/agents/taskledger-worker-complex.toml
```

The templates currently map routine work to Luna and complex work to Terra.
Those mappings are defaults, not Taskledger requirements. Edit `model` and
`model_reasoning_effort` in each file to match the models available to the
consuming repository.

Configure the stronger primary model and assignment concurrency in the target
repository's `.codex/config.toml`:

```toml
model = "gpt-5.6-sol"
model_reasoning_effort = "medium"

[agents]
enabled = true
max_concurrent_threads_per_session = 1
```

Taskledger does not require these exact model names. It requires the primary to
be capable of decomposition and independent review, plus named `routine` and
`complex` workers with the stable names in the templates. Start a new Codex task
after installing or changing the skill or agent files. Medium is the recommended
starting effort for a capable primary; raise it only when the specification or
review boundary actually warrants more reasoning.

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
3. Install the two worker profiles as described above.
4. Put the specification in a file the primary agent can read.
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

Ask the primary Codex agent to use the installed skill and name the specification:

```text
Use $taskledger to implement docs/my-spec.md. Inspect the repository, propose
the plan, and stop for the required approvals before making changes.
```

For a new or materially changed plan, Taskledger uses two approval gates:

1. **Plan approval:** outcome, success criteria, scope, dependencies, task
   boundaries, assumptions, and proposed parallel work.
2. **Execution approval:** exact branch and Git state, initialization and commit
   actions, validation result, and the first assignment wave.

The primary then creates only the approved assignments. Workers implement in
isolated worktrees and submit through Taskledger; they do not integrate their
own work. The primary independently checks the exact submitted commit before
recording verification and integration.

Task definitions may include exact `required_checks`. Use task-focused checks
for intermediate slices, put strict lint/generated-contract checks on the first
slice that can break them, and reserve the complete cross-project suite for the
integrated final audit. A submission cannot be recorded until its evidence
reports every required command with exit code zero; the primary still reruns
those checks independently against each submitted state. For a larger new plan,
`plan apply` creates a batch of requirements and tasks transactionally using
short local references, reducing repetitive CLI traffic before `plan validate`.

To resume later, ask Codex to resume the existing Taskledger project. The ledger
persists the approved plan and completed work; the chat transcript is not the
source of truth.

For installation variants, routine operation, updates, backup and restore,
troubleshooting, and removal, use the [complete run guide](docs/RUN_GUIDE.md).

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

After the final implementation integration, the primary performs the integrated
cross-task audit and complete quality suite directly. Do not create a generic
“final hardening” worker whose main job is to review already integrated work;
create narrowly scoped correction assignments only for defects the primary
audit actually finds.

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
printf '{}\n' | taskledger project cleanup --input -
```

The first `project init` call is discovery-only. Confirmed initialization and all
later mutations are normally driven by the Codex skill after approval. The full
command and request-body reference is in
[`skills/taskledger/references/commands.md`](skills/taskledger/references/commands.md).

Repositories created with Taskledger 0.1 may still use a shared
`~/.taskledger` store. The current version attempts best-effort discovery; set
`TASKLEDGER_HOME=~/.taskledger` explicitly only when opening one of those legacy
projects.

## Development

Run the test suite from a checkout:

```sh
python3 -m unittest discover -s tests -v
```

Documentation and design references:

- [Complete run guide](docs/RUN_GUIDE.md)
- [Product specification](taskledger-product-spec.md)
- [Technical specification](taskledger-technical-spec.md)
- [Command registry](COMMAND_REGISTRY.md)
- [Requirement traceability](TRACEABILITY.md)
- [Implementation handoff/checklist](taskledger-implementation-handoff.md)
- [Token-efficiency benchmarks](benchmarks/token-efficiency/README.md)
- [Publishing preflight](PUBLISHING_CHECKLIST.md)

## Limitations

- The full failure-injection and 71-requirement conformance suite is incomplete.
- Recovery guarantees should not be trusted for high-risk or irreplaceable work.
- Windows/WSL and non-Codex orchestration adapters are untested or absent.
- Taskledger cannot observe provider token counters or prices; token and cost
  comparisons require measurements from the model host.
- The plugin is not yet published through a marketplace.

## Support and maintenance

Taskledger is maintained by [Christopher Caldwell](https://github.com/christopher-caldwell).
For setup questions, reproducible bugs, or documentation problems, open a
[GitHub issue](https://github.com/christopher-caldwell/taskledger/issues). Do not
include Taskledger credentials, private source, or provider usage exports in an
issue.

## License

Taskledger is open-source software licensed under the [MIT License](LICENSE).
