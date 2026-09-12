"""Framework-neutral committed-state observation hooks."""
from __future__ import annotations

import threading
from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class ChangeScope(str, Enum):
    PROJECT = "PROJECT"
    PREPARATION = "PREPARATION"
    RUN = "RUN"
    TASKS = "TASKS"
    SESSIONS = "SESSIONS"
    REVIEWS = "REVIEWS"
    USAGE = "USAGE"
    INTERVENTIONS = "INTERVENTIONS"
    HISTORY = "HISTORY"


ALL_SCOPES = frozenset(ChangeScope)


@dataclass(frozen=True)
class ChangeNotice:
    scopes: frozenset[ChangeScope] = ALL_SCOPES
    subject_ids: tuple[str, ...] = ()


class ChangeSink(Protocol):
    def publish(self, notice: ChangeNotice) -> None: ...


class NullChangeSink:
    def publish(self, notice: ChangeNotice) -> None:
        pass


_lock = threading.Lock()
_sinks: dict[int, ChangeSink] = {}


def bind_change_sink(connection: object, sink: ChangeSink) -> None:
    with _lock:
        _sinks[id(connection)] = sink


def unbind_change_sink(connection: object) -> None:
    with _lock:
        _sinks.pop(id(connection), None)


def publish_committed(connection: object) -> None:
    with _lock:
        sink = _sinks.get(id(connection))
    if sink is not None:
        try:
            sink.publish(ChangeNotice())
        except Exception:
            # Observation is never part of the durable operation's outcome.
            pass
