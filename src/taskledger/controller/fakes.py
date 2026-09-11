from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .model import (
    ExecutionStatus,
    ExecutionView,
    ReviewPreparation,
    ReviewVerdict,
    RuntimeSession,
    RuntimeTurnHandle,
    RuntimeTurnInspection,
    RuntimeTurnResult,
    SessionRole,
    Usage,
)


@dataclass(frozen=True)
class TurnScript:
    effect: Callable[[], None] | None = None
    structured_output: dict[str, Any] | None = None
    final_response: str | None = None
    usage: Usage = Usage()
    failure: str | None = None
    uncertain: bool = False


class FakeRuntime:
    def __init__(self, *, worker_turns: list[TurnScript], reviewer_turns: list[TurnScript]):
        self.scripts = {SessionRole.WORKER: list(worker_turns), SessionRole.REVIEWER: list(reviewer_turns)}
        self.roles: dict[str, SessionRole] = {}
        self.turns: dict[tuple[str, str], tuple[str, TurnScript]] = {}
        self.sessions_started = 0
        self.turns_started = 0

    async def start_session(self, *, role, profile, subject_id, cwd, writable):
        self.sessions_started += 1
        thread = f"thread-{self.sessions_started}"
        self.roles[thread] = role
        return RuntimeSession(thread)

    async def resume_session(self, *, thread_id, role, profile, subject_id, cwd, writable):
        if thread_id not in self.roles:
            raise RuntimeError("thread does not exist")
        return RuntimeSession(thread_id)

    async def start_turn(self, *, thread_id, prompt, output_schema=None):
        role = self.roles[thread_id]
        if not self.scripts[role]:
            raise RuntimeError("no scripted turn")
        self.turns_started += 1
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
        return RuntimeTurnResult(handle, script.structured_output, script.final_response, script.usage)

    async def inspect_turn(self, handle):
        state, script = self.turns.get((handle.thread_id, handle.turn_id), ("UNKNOWN", TurnScript()))
        if script.uncertain or state == "UNKNOWN":
            return RuntimeTurnInspection("UNKNOWN", error="outcome unknown")
        if state == "FAILED":
            return RuntimeTurnInspection("FAILED", error=script.failure)
        if state == "COMPLETED":
            return RuntimeTurnInspection("COMPLETED", RuntimeTurnResult(handle, script.structured_output, script.final_response, script.usage))
        return RuntimeTurnInspection("RUNNING")


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
