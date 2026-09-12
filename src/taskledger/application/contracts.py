from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SnapshotVersion:
    engine_epoch: str
    revision: int


@dataclass(frozen=True)
class ApplicationError:
    code: str
    message: str
    details: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class OperationReceipt:
    operation_id: str
    disposition: str
    run_id: str | None = None
    preparation_id: str | None = None
    error: ApplicationError | None = None


@dataclass(frozen=True)
class PauseRequestReceipt:
    lifecycle_id: str
    status: str


@dataclass(frozen=True)
class Record:
    """Immutable, JSON-compatible projected row."""
    fields: tuple[tuple[str, Any], ...]

    def get(self, name: str, default: Any = None) -> Any:
        return dict(self.fields).get(name, default)


@dataclass(frozen=True)
class ConsoleSnapshot:
    schema_version: int
    version: SnapshotVersion
    projected_at: str
    project: Record
    preparation: Record | None
    run: Record | None
    tasks: tuple[Record, ...]
    sessions: tuple[Record, ...]
    usage: Record
    interventions: tuple[Record, ...]
    activity: tuple[Record, ...]
    operation: Record | None = None


@dataclass(frozen=True)
class HistoryCursor:
    audit_sequence: int = 0
    controller_sequence: int = 0


@dataclass(frozen=True)
class HistoryPage:
    records: tuple[Record, ...]
    cursor: HistoryCursor


@dataclass(frozen=True)
class OperationStatus:
    operation_id: str
    phase: str
    receipt: OperationReceipt | None
    result: Record | None = None
    error: ApplicationError | None = None
