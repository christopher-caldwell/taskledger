from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .model import (
    ExecutionStatus,
    ExecutionView,
    ReviewPreparation,
    ReviewVerdict,
    RuntimeSession,
    RuntimeIdentity,
    RuntimeTurnHandle,
    RuntimeTurnInspection,
    RuntimeTurnResult,
    SessionRole,
    Usage,
    UsagePrecision,
)


@dataclass(frozen=True)
class TurnScript:
    effect: Callable[[], None] | None = None
    structured_output: dict[str, Any] | Callable[[], dict[str, Any] | None] | None = None
    final_response: str | None = None
    usage: Usage = Usage()
    failure: str | None = None
    uncertain: bool = False
    usage_missing: bool = False


class FakeRuntime:
    def __init__(self, *, worker_turns: list[TurnScript], reviewer_turns: list[TurnScript], final_reviewer_turns: list[TurnScript] | None = None, task_creator_turns: list[TurnScript] | None = None):
        self.scripts = {
            SessionRole.WORKER: list(worker_turns),
            SessionRole.REVIEWER: list(reviewer_turns),
            SessionRole.REQUIREMENT_REVIEWER: list(final_reviewer_turns or []),
            SessionRole.TASK_CREATOR: list(task_creator_turns or []),
        }
        self.roles: dict[str, SessionRole] = {}
        self.turns: dict[tuple[str, str], tuple[str, TurnScript]] = {}
        self.sessions_started = 0
        self.turns_started = 0
        self.worker_tools: dict[str, Any] = {}
        self.identity_overrides: dict[tuple[SessionRole, str], RuntimeIdentity] = {}
        self.prompts: list[dict[str, Any]] = []
        self.provider_histories: dict[str, dict[str, Any] | Exception] = {}
        self.interrupted: list[RuntimeTurnHandle] = []
        self.protocol_identity = "fake-runtime-v1"
        self.closed = False

    def session_identity(self, *, role, profile, subject_id, cwd, writable):
        return self.identity_overrides.get(
            (role, profile),
            RuntimeIdentity(
                model=f"fake-{profile}",
                effort="test",
                agent_config_hash=f"hash-{profile}",
                sandbox={"type": "workspaceWrite" if writable else "readOnly", "cwd": cwd},
                protocol_identity="fake-runtime-v1",
            ),
        )

    async def start_session(self, *, role, profile, subject_id, cwd, writable):
        self.sessions_started += 1
        thread = f"thread-{self.sessions_started}"
        self.roles[thread] = role
        return RuntimeSession(thread, self.session_identity(role=role, profile=profile, subject_id=subject_id, cwd=cwd, writable=writable))

    async def resume_session(self, *, thread_id, role, profile, subject_id, cwd, writable, cumulative_usage_baseline=None):
        if thread_id not in self.roles:
            raise RuntimeError("thread does not exist")
        return RuntimeSession(thread_id, self.session_identity(role=role, profile=profile, subject_id=subject_id, cwd=cwd, writable=writable))

    async def start_turn(self, *, thread_id, prompt, output_schema=None):
        role = self.roles[thread_id]
        if not self.scripts[role]:
            raise RuntimeError("no scripted turn")
        self.turns_started += 1
        self.prompts.append({"thread_id": thread_id, "prompt": prompt, "output_schema": output_schema})
        turn_id = f"turn-{self.turns_started}"
        self.turns[(thread_id, turn_id)] = ("RUNNING", self.scripts[role].pop(0))
        return RuntimeTurnHandle(thread_id, turn_id)

    async def wait_turn(self, handle):
        state, script = self.turns[(handle.thread_id, handle.turn_id)]
        if script.uncertain:
            raise RuntimeError("outcome unknown")
        if script.failure:
            self.turns[(handle.thread_id, handle.turn_id)] = ("FAILED", script)
            raise RuntimeError(script.failure)
        if script.effect:
            script.effect()
        self.turns[(handle.thread_id, handle.turn_id)] = ("COMPLETED", script)
        structured = script.structured_output() if callable(script.structured_output) else script.structured_output
        precision = UsagePrecision.MISSING if script.usage_missing else UsagePrecision.THREAD_TOTAL_DELTA
        return RuntimeTurnResult(handle, structured, script.final_response, script.usage, script.usage_missing, precision)

    async def inspect_turn(self, handle):
        state, script = self.turns.get((handle.thread_id, handle.turn_id), ("UNKNOWN", TurnScript()))
        precision = UsagePrecision.MISSING if script.usage_missing else UsagePrecision.THREAD_TOTAL_DELTA
        result = RuntimeTurnResult(
            handle,
            script.structured_output() if callable(script.structured_output) else script.structured_output,
            script.final_response,
            script.usage,
            script.usage_missing,
            precision,
        )
        if script.uncertain or state == "UNKNOWN":
            partial = result
            if script.usage.total_tokens or script.usage.cached_input_tokens or script.usage.cache_write_input_tokens or script.usage.reasoning_tokens:
                partial = RuntimeTurnResult(
                    handle,
                    usage=script.usage,
                    usage_missing=True,
                    usage_precision=UsagePrecision.PARTIAL_OBSERVATION,
                )
            return RuntimeTurnInspection("UNKNOWN", partial if script.usage_missing or script.usage != Usage() else None, "outcome unknown")
        if state == "FAILED":
            return RuntimeTurnInspection("FAILED", result, script.failure)
        if state == "COMPLETED":
            return RuntimeTurnInspection("COMPLETED", result)
        return RuntimeTurnInspection("RUNNING")

    async def interrupt_turn(self, handle):
        self.interrupted.append(handle)
        state, script = self.turns.get((handle.thread_id, handle.turn_id), ("RUNNING", TurnScript()))
        self.turns[(handle.thread_id, handle.turn_id)] = ("FAILED", script)

    def usage_events(self, handle):
        result = self.turns.get((handle.thread_id, handle.turn_id))
        if not result:
            return ()
        usage = result[1].usage
        return ({"event_id": f"fake-{handle.turn_id}", "usage": usage.__dict__, "raw": {"turn_id": handle.turn_id}},)

    async def provider_history(self, thread_id):
        value = self.provider_histories.get(thread_id, {"turns": [], "items": []})
        if isinstance(value, Exception):
            raise value
        return value

    async def recover_terminal_usage(
        self, *, handle, previous_turn_id, cumulative_before, target_is_first,
        role, profile, subject_id, cwd, writable,
    ):
        value = self.provider_histories.get(handle.thread_id)
        if not isinstance(value, dict):
            return None
        turns = value.get("turns", [])
        if not turns or turns[0].get("id") != handle.turn_id:
            return None
        if turns[0].get("status", "completed") not in {"completed", "failed", "interrupted"}:
            return None
        if target_is_first:
            if len(turns) != 1 or value.get("nextCursor"):
                return None
        elif len(turns) < 2 or turns[1].get("id") != previous_turn_id:
            return None
        after = value.get("cumulative_usage")
        if not isinstance(after, Usage):
            return None
        try:
            usage = after.subtract(cumulative_before)
        except ValueError:
            return None
        return RuntimeTurnResult(
            handle, usage=usage, usage_precision=UsagePrecision.THREAD_TOTAL_DELTA,
            cumulative_before=cumulative_before, cumulative_after=after,
        )

    async def close(self):
        self.closed = True


class FakeLedger:
    def __init__(self, *, assignment_id: str = "a1", criteria: tuple[str, ...] = ("c1",), profile: str = "routine"):
        self.assignment_id = assignment_id
        self.criteria = criteria
        self.profile = profile
        self.status = ExecutionStatus.ACTIVE
        self.progress_count = 0
        self.submission_id: str | None = None
        self.correction_packet: dict[str, Any] | None = None
        self.acceptance_allowed = True
        self.applied: list[ReviewVerdict] = []

    def execution_view(self, assignment_id):
        return ExecutionView(assignment_id, self.status, self.profile, self.submission_id, self.correction_packet)

    def progress_fingerprint(self, assignment_id):
        return f"{self.progress_count}:{self.status.value}:{self.submission_id}"

    def worker_prompt(self, assignment_id, *, first_turn):
        return "start" if first_turn else "continue"

    def worker_cwd(self, assignment_id):
        return "/tmp"

    def submission_pending(self, submission_id):
        return self.status == ExecutionStatus.SUBMITTED and self.submission_id == submission_id

    def prepare_review(self, submission_id):
        return ReviewPreparation(submission_id, self.criteria, self.acceptance_allowed, {"submission_id": submission_id})

    def reviewer_prompt(self, preparation):
        return "review"

    def reviewer_cwd(self, submission_id):
        return "/tmp"

    def reviewer_output_schema(self, preparation):
        return {"type": "object"}

    def apply_review(self, submission_id, verdict):
        self.applied.append(verdict)
        if verdict.outcome.value == "ACCEPTED":
            self.status = ExecutionStatus.COMPLETED
        elif verdict.outcome.value == "REJECTED":
            self.status = ExecutionStatus.ACTIVE
            self.submission_id = None
            self.correction_packet = {"corrections": verdict.corrections}
        else:
            self.status = ExecutionStatus.BLOCKED
        return self.execution_view(self.assignment_id)

    def progress(self):
        self.progress_count += 1

    def submit(self):
        self.progress_count += 1
        self.submission_id = f"s{self.progress_count}"
        self.status = ExecutionStatus.SUBMITTED

    def block(self):
        self.status = ExecutionStatus.BLOCKED


def accepted_verdict(criteria: tuple[str, ...] = ("c1",)) -> dict[str, Any]:
    return {
        "outcome": "ACCEPTED",
        "criterion_results": [{"criterion_id": item, "satisfied": True, "evidence": "verified"} for item in criteria],
        "behavior_matches_intent": True,
        "required_evidence_present": True,
        "blocking_issues_remaining": False,
        "corrections": None,
        "notes": "accepted",
        "blocker_id": None,
        "blocker": None,
    }


def rejected_verdict(criteria: tuple[str, ...] = ("c1",)) -> dict[str, Any]:
    return {
        "outcome": "REJECTED",
        "criterion_results": [{"criterion_id": item, "satisfied": False, "evidence": "defect"} for item in criteria],
        "behavior_matches_intent": False,
        "required_evidence_present": True,
        "blocking_issues_remaining": False,
        "corrections": "fix the defect",
        "notes": "rejected",
        "blocker_id": None,
        "blocker": None,
    }
