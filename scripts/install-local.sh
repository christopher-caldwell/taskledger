#!/bin/sh
set -eu

taskledger_repo=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
taskledger_codex_root=${CODEX_HOME:-"$HOME/.codex"}
taskledger_skill_target="$taskledger_codex_root/skills/taskledger"

command -v uv >/dev/null 2>&1 || {
  echo "uv is required to install the Taskledger CLI." >&2
  exit 1
}
command -v rsync >/dev/null 2>&1 || {
  echo "rsync is required to synchronize the Taskledger skill." >&2
  exit 1
}

uv tool install --force "$taskledger_repo"
mkdir -p "$taskledger_skill_target"
rsync -a --delete "$taskledger_repo/skills/taskledger/" "$taskledger_skill_target/"

taskledger --version
cmp "$taskledger_repo/skills/taskledger/SKILL.md" "$taskledger_skill_target/SKILL.md"
echo "Installed Taskledger CLI and synchronized skill at $taskledger_skill_target"
