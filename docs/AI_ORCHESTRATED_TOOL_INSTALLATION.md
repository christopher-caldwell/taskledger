# Installing Taskledger as an AI Orchestrated Tool

This document explains the exact Taskledger installation, including the local
command, Codex instructions, plugin package, worker profiles, and project state.
It is written for a model building a similar tool whose operations will be
planned, invoked, and reviewed by another model.

## Installation is a set of contracts

Taskledger is not one executable copied to one directory. Five surfaces work
together:

| Surface | Source | Installed location | Responsibility |
| --- | --- | --- | --- |
| Python CLI | `src/taskledger/` and `pyproject.toml` | Active Python environment or `uv` tool environment | Owns validation, durable state, credentials, Git operations, and JSON responses |
| Codex skill | `skills/taskledger/` | `${CODEX_HOME:-$HOME/.codex}/skills/taskledger/` | Tells the primary model when and how to call the CLI |
| Plugin package | `.codex-plugin/plugin.json` plus `skills/` | A local marketplace plugin source, currently `$HOME/plugins/taskledger/` | Makes the skill installable and visible through Codex plugin management |
| Worker profiles | `skills/taskledger/assets/*.toml` | `<managed-repository>/.codex/agents/` | Defines the named implementation agents that the primary may launch |
| Project state | Created by `taskledger project init` | `<managed-repository>/.taskledger/` | Stores the ledger, credentials, worktrees, receipts, artifacts, and exports |

The CLI is the authority. The skill and profiles are model guidance. The plugin
is a distribution wrapper. Project state is private runtime data and is never
part of the plugin archive.

## Current installation on this machine

The 0.6.0 development installation created from this checkout has these exact
locations:

```text
source checkout:
  /Users/christophercaldwell/Code/projects/task_ledger

active executable:
  /Library/Frameworks/Python.framework/Versions/3.13/bin/taskledger

Python package mode:
  editable 0.6.0, pointing at the source checkout

direct Codex skill:
  /Users/christophercaldwell/.codex/skills/taskledger

personal marketplace:
  /Users/christophercaldwell/.agents/plugins/marketplace.json

personal plugin source:
  /Users/christophercaldwell/plugins/taskledger

installed Codex plugin cache:
  /Users/christophercaldwell/.codex/plugins/cache/personal/taskledger/0.6.0+codex.20260910160856
```

The installed plugin identifier is `taskledger@personal`. Worker profiles were
not placed in a global directory. They remain templates because each managed
repository must select its own models and preserve any local profile changes.

## Version invariant

Before installation, the same base release must appear in these files:

```text
pyproject.toml                         project.version
src/taskledger/__init__.py             __version__
skills/taskledger/SKILL.md             metadata.version
.codex-plugin/plugin.json              version
```

For this release the base version is `0.6.0`. A local installed plugin may use a
single cache suffix such as `0.6.0+codex.20260910160856`. The suffix tells
Codex to reload changed local content. It does not change the Taskledger protocol
version.

Do not support two protocol generations from one installation. Taskledger 0.6
opens current project stores only. An explicit `TASKLEDGER_HOME` selects an
external current store; it is not an older store discovery mechanism.

## 1. Check the source and active installation

Run these commands from the Taskledger checkout:

```sh
git status --short
python3 --version
command -v taskledger || true
which -a taskledger || true
python3 -m pip show taskledger || true
uv tool list || true
```

The important question is which installer owns the executable first on `PATH`.
Update that installation. Do not create a second installation with another
package manager and assume the shell will choose the new one.

## 2. Install the CLI

For a normal source installation:

```sh
python3 -m pip install --upgrade /absolute/path/to/task_ledger
```

For development, where the executable should load the checkout directly:

```sh
python3 -m pip install --editable /absolute/path/to/task_ledger
```

For an existing `uv` tool installation:

```sh
uv tool install --force /absolute/path/to/task_ledger
```

Verify both code and package metadata:

```sh
taskledger --version
python3 -m pip show taskledger
```

The first command must return the Taskledger JSON envelope with version `0.6.0`.
When `pip` owns the active executable, the second command must also report
`Version: 0.6.0`.

## 3. Install the direct Codex skill

Use `rsync` because an update may remove obsolete files:

```sh
mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills/taskledger"
rsync -a --delete \
  /absolute/path/to/task_ledger/skills/taskledger/ \
  "${CODEX_HOME:-$HOME/.codex}/skills/taskledger/"
```

Verify the copy rather than trusting the command exit alone:

```sh
cmp \
  /absolute/path/to/task_ledger/skills/taskledger/SKILL.md \
  "${CODEX_HOME:-$HOME/.codex}/skills/taskledger/SKILL.md"
```

This direct copy is the simplest development path. A new Codex thread is needed
after changing a skill because a running thread may retain its original skill
snapshot.

## 4. Install or refresh the personal plugin

The current personal marketplace is:

```text
marketplace file: $HOME/.agents/plugins/marketplace.json
marketplace name: personal
plugin source: $HOME/plugins/taskledger
plugin identifier: taskledger@personal
```

These are the current expected values. Never edit the marketplace file by hand
during an update.

Synchronize the plugin source from the checkout:

```sh
mkdir -p "$HOME/plugins/taskledger/.codex-plugin"
mkdir -p "$HOME/plugins/taskledger/skills"
rsync -a --delete \
  /absolute/path/to/task_ledger/.codex-plugin/ \
  "$HOME/plugins/taskledger/.codex-plugin/"
rsync -a --delete \
  /absolute/path/to/task_ledger/skills/ \
  "$HOME/plugins/taskledger/skills/"
```

Validate the source with the plugin creation tools:

```sh
python3 "${CODEX_HOME:-$HOME/.codex}/skills/.system/plugin-creator/scripts/validate_plugin.py" \
  "$HOME/plugins/taskledger"
python3 "${CODEX_HOME:-$HOME/.codex}/skills/.system/plugin-creator/scripts/read_marketplace_name.py"
```

For a local update, add one fresh cache suffix to the installed plugin manifest:

```sh
python3 "${CODEX_HOME:-$HOME/.codex}/skills/.system/plugin-creator/scripts/update_plugin_cachebuster.py" \
  "$HOME/plugins/taskledger"
codex plugin add taskledger@personal
codex plugin list
```

The cache tool preserves `0.6.0` and replaces any previous suffix. The final list
must show `taskledger@personal` as installed and enabled. Start a new thread to
test the refreshed plugin.

The plugin path is optional for a tool distributed only as a direct skill. For a
marketplace plugin, it is the preferred user installation surface. Do not list
an MCP server or app in `plugin.json` unless its companion manifest exists.

## 5. Install worker profiles per managed repository

Taskledger deliberately does not overwrite worker profiles during a global
refresh. Each consuming repository owns its model choices and local additions.

For a new managed repository:

```sh
mkdir -p /absolute/path/to/managed-repository/.codex/agents
cp "${CODEX_HOME:-$HOME/.codex}/skills/taskledger/assets/taskledger-worker-routine.toml" \
  /absolute/path/to/managed-repository/.codex/agents/taskledger-worker-routine.toml
cp "${CODEX_HOME:-$HOME/.codex}/skills/taskledger/assets/taskledger-worker-complex.toml" \
  /absolute/path/to/managed-repository/.codex/agents/taskledger-worker-complex.toml
```

For an existing repository, compare and merge the templates. Preserve the stable
profile names, scoped Taskledger instructions, and any repository model settings.
Do not replace a customized profile without inspecting the diff.

## 6. Prepare project state

Before initialization, the managed repository must ignore Taskledger runtime
state:

```sh
printf '\n.taskledger/\n' >> /absolute/path/to/managed-repository/.gitignore
git -C /absolute/path/to/managed-repository check-ignore -v .taskledger/
```

Commit the ignore rule when appropriate. Then discovery and initialization are
separate operations:

```sh
taskledger project init \
  --repo /absolute/path/to/managed-repository \
  --input - <<'JSON'
{}
JSON

taskledger project init \
  --repo /absolute/path/to/managed-repository \
  --confirm-branch exact-detected-branch \
  --input - <<'JSON'
{}
JSON
```

The first call must not create state. The second call is allowed only after the
human approves the exact branch and mutation. Never commit `.taskledger/`, print
credentials, or copy the state directory into a plugin.

## 7. Verify the complete installation

Run this checklist:

```sh
taskledger --version
python3 -m taskledger --version
cmp \
  /absolute/path/to/task_ledger/skills/taskledger/SKILL.md \
  "${CODEX_HOME:-$HOME/.codex}/skills/taskledger/SKILL.md"
codex plugin list
python3 -m unittest -v
```

Also inspect one consuming repository and confirm that both named profile files
exist. In a new Codex thread, ask the model to identify the Taskledger skill and
run read only project discovery. Do not use a state changing command as the first
plugin smoke test.

## Blueprint for a similar AI orchestrated tool

Use the same separation when building another tool:

1. Put authority in a deterministic program with strict input validation,
   stable machine responses, scoped credentials, and durable state.
2. Put orchestration policy in a skill. State when the model may mutate data,
   what requires human approval, and which output is evidence versus a claim.
3. Put delegated roles in named profiles. Give each role the minimum authority
   and enough persisted context to start without inherited chat history.
4. Wrap the skill, MCP servers, or apps in a valid plugin manifest. Declare only
   components that exist and validate every relative path.
5. Use a marketplace entry for discovery and policy. Keep source paths local and
   update local plugins through a cache suffix plus reinstall, not manual cache
   edits.
6. Keep project runtime state outside the plugin package and source control.
7. Verify every installed surface independently. An executable version, package
   metadata, skill metadata, plugin version, profile version, and state schema can
   drift even when one smoke test passes.

The model that installs such a tool should report each surface it changed, the
exact verification result, and any surface intentionally left for a consuming
repository to customize.
