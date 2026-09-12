from __future__ import annotations

import os
from pathlib import Path

from .core import taskledger_home


def home_for_repository(info, *, create: bool) -> Path:
    """Resolve storage for an inspected repository without adapter dependencies."""
    if os.environ.get("TASKLEDGER_HOME"):
        return taskledger_home(create=create)
    common = Path(str(info["common"]))
    repository_root = common.parent if common.name == ".git" else Path(str(info["root"]))
    return taskledger_home(repository_root, create=create)
