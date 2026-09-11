from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SessionRole(str, Enum):
    TASK_CREATOR = "TASK_CREATOR"
    WORKER = "WORKER"
    REVIEWER = "REVIEWER"
    REQUIREMENT_REVIEWER = "REQUIREMENT_REVIEWER"


class ExecutionStatus(str, Enum):
    ACTIVE = "ACTIVE"
    CHECKPOINT = "CHECKPOINT"
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
    CONFIGURATION_DRIFT = "CONFIGURATION_DRIFT"
    PLAN_INVALID = "PLAN_INVALID"
    PROJECT_INCOMPLETE = "PROJECT_INCOMPLETE"
    INVALID_STATE = "INVALID_STATE"
    USER_INTERRUPTED = "USER_INTERRUPTED"
    PROCESS_TERMINATED = "PROCESS_TERMINATED"


class TurnExecutionState(str, Enum):
    COMPLETED = "COMPLETED"
    KNOWN_FAILED = "KNOWN_FAILED"
    UNCERTAIN = "UNCERTAIN"


class UsagePrecision(str, Enum):
    EXACT_RESPONSES = "EXACT_RESPONSES"
    THREAD_TOTAL_DELTA = "THREAD_TOTAL_DELTA"
    PARTIAL_OBSERVATION = "PARTIAL_OBSERVATION"
    SYNTHETIC_OR_ESTIMATED = "SYNTHETIC_OR_ESTIMATED"
    MISSING = "MISSING"
    LEGACY_LAST_USAGE = "LEGACY_LAST_USAGE"


class DispatchReason(str, Enum):
    INITIAL_PLANNING = "INITIAL_PLANNING"
    INITIAL_WORK = "INITIAL_WORK"
    ACTIVE_CONTINUATION = "ACTIVE_CONTINUATION"
    CORRECTION = "CORRECTION"
    CHECKPOINT_CONTINUATION = "CHECKPOINT_CONTINUATION"
    QUESTION_ANSWERED = "QUESTION_ANSWERED"
    STRUCTURED_OUTPUT_RETRY = "STRUCTURED_OUTPUT_RETRY"
    SUBMISSION_REVIEW = "SUBMISSION_REVIEW"
    CHECKPOINT_REVIEW = "CHECKPOINT_REVIEW"
    FINAL_REVIEW = "FINAL_REVIEW"
    CORRECTION_PLANNING = "CORRECTION_PLANNING"


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cache_write_input_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            self.input_tokens + other.input_tokens,
            self.cached_input_tokens + other.cached_input_tokens,
            self.output_tokens + other.output_tokens,
            self.reasoning_tokens + other.reasoning_tokens,
            self.cache_write_input_tokens + other.cache_write_input_tokens,
        )

    def subtract(self, before: "Usage") -> "Usage":
        values = (
            self.input_tokens - before.input_tokens,
            self.cached_input_tokens - before.cached_input_tokens,
            self.output_tokens - before.output_tokens,
            self.reasoning_tokens - before.reasoning_tokens,
            self.cache_write_input_tokens - before.cache_write_input_tokens,
        )
        if any(value < 0 for value in values):
            raise ValueError("cumulative token usage moved backwards")
        return Usage(*values)


@dataclass(frozen=True)
class RuntimeIdentity:
    model: str
    effort: str
    agent_config_hash: str
    sandbox: dict[str, Any]
    protocol_identity: str
    profile_name: str | None = None
    profile_role_name: str | None = None
    profile_source_kind: str | None = None
    profile_source_file: str | None = None
    profile_hash: str | None = None
    capability_policy_hash: str | None = None
    base_instruction_bytes: int = 0
    profile_instruction_bytes: int = 0
    dynamic_tool_schema_bytes: int = 0
    dynamic_tool_count: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "effort": self.effort,
            "agent_config_hash": self.agent_config_hash,
            "sandbox": self.sandbox,
            "protocol_identity": self.protocol_identity,
            "profile_name": self.profile_name,
            "profile_role_name": self.profile_role_name,
            "profile_source_kind": self.profile_source_kind,
            "profile_source_file": self.profile_source_file,
            "profile_hash": self.profile_hash,
            "capability_policy_hash": self.capability_policy_hash,
            "base_instruction_bytes": self.base_instruction_bytes,
            "profile_instruction_bytes": self.profile_instruction_bytes,
            "dynamic_tool_schema_bytes": self.dynamic_tool_schema_bytes,
            "dynamic_tool_count": self.dynamic_tool_count,
        }


@dataclass(frozen=True)
class PromptPacket:
    text: str
    dispatch_reason: DispatchReason
    prompt_builder_version: str = "controller-prompt-v2"
    controller_payload_bytes: int = 0
    static_assignment_bytes: int = 0
    dynamic_state_bytes: int = 0
    correction_bytes: int = 0
    output_schema_bytes: int = 0
    dynamic_state_hash: str | None = None
    context_hashes: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class RuntimeSession:
    thread_id: str
    identity: RuntimeIdentity | None = None


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
    usage_missing: bool = False
    usage_precision: UsagePrecision = UsagePrecision.MISSING
    cumulative_before: Usage | None = None
    cumulative_after: Usage | None = None
    exact_response_count: int = 0


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
    pending_checkpoint_id: str | None = None


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
