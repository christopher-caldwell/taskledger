# Taskledger

Taskledger is a local, durable execution ledger for Codex projects. It records
requirements, task definitions, scoped assignments, submissions, independent
verification, Git integration, blockers, recovery state, and requirement-based
completion. It does not plan work or decide whether an implementation is correct.

This repository contains both the Python CLI and the Codex plugin that teaches
Codex how to use it safely.

## Requirements

- Python 3.11 or later
- Git
- Codex, for the optional plugin

## Install the CLI

Clone this repository and install it into your preferred Python environment:

```sh
git clone https://github.com/christopher-caldwell/taskledger.git
cd taskledger
python3 -m pip install .
taskledger project show
```

For development, use an editable install instead:

```sh
python3 -m pip install -e .
```

## Install the Codex skill

After installing the CLI, copy the included skill into Codex's local skills
directory, then start a new Codex thread so it is picked up:

```sh
mkdir -p ~/.codex/skills
cp -R skills/taskledger ~/.codex/skills/taskledger
```

The repository also includes an installable plugin manifest at
`.codex-plugin/plugin.json` for marketplace-based installation.

If you only need the CLI, the plugin is optional. The CLI is the source of
truth for Taskledger state and emits a single JSON envelope on stdout for every
command.

## Validate

```sh
python3 -m unittest discover -s tests -v
```

## Status

Taskledger v0.1.0 is experimental. Its happy-path acceptance suite passes, but
the full failure-injection and 66-requirement conformance suite is not complete.
Do not rely on its recovery guarantees for high-risk or irreplaceable work.

## License

No license has been granted yet. Contact the author before redistributing or
using this source outside your own environment.
