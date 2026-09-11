from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import stat
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable


MAX_JSON = 10 * 1024 * 1024
MAX_TEXT = 1024 * 1024
MAX_SPEC = 50 * 1024 * 1024


class LedgerError(Exception):
    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None,
                 actions: list[str] | None = None, exit_code: int | None = None):
        super().__init__(message)
        self.code, self.message = code, message
        self.details = details or {}
        self.actions = actions or []
        self.exit_code = exit_code or error_exit(code)


def error_exit(code: str) -> int:
    if code in {"AUTHENTICATION_FAILED", "AUTHORIZATION_DENIED", "WORKER_SCOPE_VIOLATION"}:
        return 4
    if code in {"GIT_COMMAND_FAILED", "NOT_A_GIT_REPOSITORY", "BARE_REPOSITORY_UNSUPPORTED",
                "DETACHED_HEAD", "CANONICAL_BRANCH_NOT_FOUND", "CANONICAL_BRANCH_NOT_CHECKED_OUT",
                "CANONICAL_WORKTREE_DIRTY", "INTEGRATION_FAILED_SAFE", "INITIAL_COMMIT_REQUIRED",
                "LEDGER_DIRECTORY_NOT_IGNORED"}:
        return 5
    if code in {"RECOVERY_REQUIRED", "INTEGRATION_STATE_UNCERTAIN"}:
        return 6
    if code in {"INVALID_JSON", "INVALID_REQUEST", "UNKNOWN_FIELD", "PLAN_VALIDATION_FAILED"}:
        return 2
    return 3


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def new_id() -> str:
    return str(uuid.uuid4())


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_bytes(value: Any) -> bytes:
    return canonical(value).encode("utf-8")


def sha256(value: bytes | str) -> str:
    if isinstance(value, str):
        value = value.encode()
    return hashlib.sha256(value).hexdigest()


def token() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")


def taskledger_home(repository_root: str | Path | None = None, *, create: bool = True) -> Path:
    configured = os.environ.get("TASKLEDGER_HOME")
    if configured:
        path = Path(configured).expanduser()
    elif repository_root is not None:
        path = Path(repository_root).resolve() / ".taskledger"
    else:
        raise LedgerError(
            "PROJECT_REQUIRED",
            "Run Taskledger from the repository or set TASKLEDGER_HOME to its configured current store.",
        )
    if not create:
        return path
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError as exc:
        raise LedgerError(
            "LEDGER_STORAGE_UNAVAILABLE",
            "Taskledger could not access this repository's local state directory.",
            details={"path": str(path), "reason": exc.__class__.__name__},
        )
    try:
        path.chmod(0o700)
    except OSError:
        pass
    return path


def atomic_secret(path: Path, value: str) -> None:
    temporary = path.with_name(path.name + ".tmp-" + new_id())
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(value + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
        try:
            path.chmod(0o600)
        except OSError:
            pass
    except OSError as exc:
        raise LedgerError(
            "LEDGER_STORAGE_UNAVAILABLE",
            "Taskledger could not write a credential in this repository's local state.",
            details={"path": str(path), "reason": exc.__class__.__name__},
        )
    finally:
        try:
            if temporary.exists():
                temporary.unlink()
        except OSError:
            pass


def require_object(value: Any, allowed: Iterable[str], required: Iterable[str] = ()) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise LedgerError("INVALID_REQUEST", "Input must be a JSON object.")
    allowed_set, required_set = set(allowed), set(required)
    unknown = sorted(set(value) - allowed_set)
    missing = sorted(required_set - set(value))
    if unknown:
        raise LedgerError("UNKNOWN_FIELD", "Input contains unknown fields.", details={"fields": unknown})
    if missing:
        raise LedgerError("INVALID_REQUEST", "Input is missing required fields.", details={"fields": missing})
    return value


def text(value: Any, name: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise LedgerError("INVALID_REQUEST", f"{name} must be a non-empty string.")
    if len(value.encode()) > MAX_TEXT:
        raise LedgerError("INVALID_REQUEST", f"{name} exceeds the 1 MiB limit.")
    return value.strip() if not allow_empty else value


def array(value: Any, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise LedgerError("INVALID_REQUEST", f"{name} must be an array.")
    return value
