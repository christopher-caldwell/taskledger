from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SessionRole(str, Enum):
    WORKER = "WORKER"
    REVIEWER = "REVIEWER"
    REQUIREMENT_REVIEWER = "REQUIREMENT_REVIEWER"


class ExecutionStatus(str, Enum):
    ACTIVE = "ACTIVE"
    SUBMITTED = "SUBMITTED"
    BLOCKED = "BLOCKED"
    REVOKED = "REVOKED"
    ACCEPTED = "ACCEPTED"
    INTEGRATION_UNCERTAIN = "INTEGRATION_UNCERTAIN"
    COMPLETED = "COMPLETED"


class ReviewOutcome(str, Enum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    BLOCKED = "BLOCKED"


class SupervisorStatus(str, Enum):
    INTEGRATED = "INTEGRATED"
    PAUSED = "PAUSED"


class PauseReason(str, Enum):
    BLOCKED = "BLOCKED"
    STALLED = "STALLED"
    MAX_TURNS = "MAX_TURNS"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    RUNTIME_FAILED = "RUNTIME_FAILED"
    RUNTIME_UNCERTAIN = "RUNTIME_UNCERTAIN"
    ESCALATION_REQUIRED = "ESCALATION_REQUIRED"
    REVIEWER_FAILURE = "REVIEWER_FAILURE"
    INTEGRATION_FAILED = "INTEGRATION_FAILED"
    INTEGRATION_UNCERTAIN = "INTEGRATION_UNCERTAIN"
    INVALID_STATE = "INVALID_STATE"


class TurnExecutionState(str, Enum):
    COMPLETED = "COMPLETED"
    KNOWN_FAILED = "KNOWN_FAILED"
    UNCERTAIN = "UNCERTAIN"


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class RuntimeSession:
    thread_id: str


@dataclass(frozen=True)
class RuntimeTurnHandle:
    thread_id: str
    turn_id: str


@dataclass(frozen=True)
class RuntimeTurnResult:
    handle: RuntimeTurnHandle
    structured_output: dict[str, Any] | None = None
    final_response: str | None = None
    usage: Usage = Usage()


@dataclass(frozen=True)
class RuntimeTurnInspection:
    state: str
    result: RuntimeTurnResult | None = None
    error: str | None = None


@dataclass(frozen=True)
class TurnExecution:
    state: TurnExecutionState
    local_turn_id: str
    result: RuntimeTurnResult | None = None
    error: str | None = None


@dataclass(frozen=True)
class ExecutionView:
    assignment_id: str
    status: ExecutionStatus
    profile: str
    pending_submission_id: str | None = None
    correction_packet: dict[str, Any] | None = None
    detail: str | None = None


@dataclass(frozen=True)
class ReviewPreparation:
    submission_id: str
    expected_criterion_ids: tuple[str, ...]
    acceptance_allowed: bool
    context: dict[str, Any]


@dataclass(frozen=True)
class ReviewVerdict:
    outcome: ReviewOutcome
    criterion_results: tuple[dict[str, Any], ...] = ()
    behavior_matches_intent: bool = False
    required_evidence_present: bool = False
    blocking_issues_remaining: bool = False
    corrections: str | None = None
    notes: str = ""
    blocker_id: str | None = None
    blocker: dict[str, str] | None = None


@dataclass(frozen=True)
class SupervisorResult:
    status: SupervisorStatus
    assignment_id: str
    pause_reason: PauseReason | None = None
    detail: str | None = None
    worker_turns: int = 0
    reviewer_turns: int = 0
    submission_reviews: int = 0
    usage: Usage = field(default_factory=Usage)
