from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Sequence

from .core import LedgerError


def run(repo: str | Path, args: Sequence[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    try:
        result = subprocess.run(["git", "-C", str(repo), *args], shell=False, text=True,
                                capture_output=True, timeout=int(os.environ.get("TASKLEDGER_GIT_TIMEOUT", "120")), env=env)
    except FileNotFoundError:
        raise LedgerError("GIT_COMMAND_FAILED", "Git executable is not available.")
    except subprocess.TimeoutExpired:
        raise LedgerError("GIT_COMMAND_FAILED", "Git command timed out.")
    if check and result.returncode:
        raise LedgerError("GIT_COMMAND_FAILED", "Git command failed.", details={"command": list(args), "stderr": result.stderr[-2000:]})
    return result


def inspect(repo: str | Path) -> dict[str, str]:
    root_result = run(repo, ["rev-parse", "--show-toplevel"], check=False)
    if root_result.returncode:
        raise LedgerError("NOT_A_GIT_REPOSITORY", "Path is not an existing Git working repository.")
    root = str(Path(root_result.stdout.strip()).resolve())
    if run(root, ["rev-parse", "--is-bare-repository"]).stdout.strip() == "true":
        raise LedgerError("BARE_REPOSITORY_UNSUPPORTED", "Bare repositories are not supported.")
    common = run(root, ["rev-parse", "--git-common-dir"]).stdout.strip()
    common_path = str((Path(root) / common).resolve()) if not Path(common).is_absolute() else str(Path(common).resolve())
    branch = run(root, ["symbolic-ref", "--quiet", "--short", "HEAD"], check=False)
    if branch.returncode:
        raise LedgerError("DETACHED_HEAD", "Repository is detached; check out an explicit branch first.")
    return {"root": root, "common": common_path, "branch": branch.stdout.strip()}


def oid(repo: str | Path, ref: str = "HEAD") -> str:
    return run(repo, ["rev-parse", "--verify", ref]).stdout.strip()


def ancestor(repo: str | Path, older: str, newer: str) -> bool:
    return run(repo, ["merge-base", "--is-ancestor", older, newer], check=False).returncode == 0


def clean(repo: str | Path) -> bool:
    return not run(repo, ["status", "--porcelain"], check=True).stdout.strip()


def current_branch(repo: str | Path) -> str | None:
    r = run(repo, ["symbolic-ref", "--quiet", "--short", "HEAD"], check=False)
    return r.stdout.strip() if r.returncode == 0 else None


def operation_in_progress(repo: str | Path) -> bool:
    git_dir = Path(run(repo, ["rev-parse", "--git-dir"]).stdout.strip())
    if not git_dir.is_absolute():
        git_dir = Path(repo) / git_dir
    return any((git_dir / n).exists() for n in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "BISECT_LOG", "rebase-merge", "rebase-apply"))
