from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass, field
from typing import Any

from taskledger import git
from taskledger.core import LedgerError, canonical, sha256

from .journal import AgentSession, Journal
from .model import DispatchReason, PauseReason, PromptPacket, RuntimeTurnHandle, SessionRole, SupervisorStatus
from .supervisor import ConfigurationDrift, Supervisor, SupervisorConfig
from .taskledger_adapter import TaskledgerLedgerAdapter
from .worker_broker import WorkerBroker


class SemanticJobUncertain(RuntimeError):
    pass


class SemanticJobBudgetExhausted(RuntimeError):
    pass


class SemanticJobInvalidOutput(RuntimeError):
    pass


@dataclass(frozen=True)
class ProjectControllerConfig:
    max_workers: int = 2
    max_reviewers: int = 1
    max_total_worker_turns: int = 100
    max_total_reviewer_turns: int = 50
    reviewer_token_reserve: int = 0
    max_final_reviewer_turns: int = 2
    max_task_creator_turns: int = 2
    max_total_task_creator_turns: int = 6
    max_inflight_targets: int | None = None
    task_creator_profile: str = "taskledger_task_creator"
    supervisor: SupervisorConfig = field(default_factory=SupervisorConfig)

    def validate(self) -> None:
        if min(self.max_workers, self.max_reviewers, self.max_total_worker_turns, self.max_total_reviewer_turns, self.max_final_reviewer_turns, self.max_task_creator_turns, self.max_total_task_creator_turns) <= 0:
            raise ValueError("project controller capacities and semantic-job limits must be positive")
        if self.max_inflight_targets is not None and self.max_inflight_targets <= 0:
            raise ValueError("max_inflight_targets must be positive")
        if self.reviewer_token_reserve < 0:
            raise ValueError("reviewer_token_reserve must not be negative")
        if self.supervisor.max_total_tokens is not None and self.reviewer_token_reserve > self.supervisor.max_total_tokens:
            raise ValueError("reviewer_token_reserve must not exceed max_total_tokens")
        self.supervisor.validate()

    def as_dict(self) -> dict[str, Any]:
        return {**asdict(self), "max_inflight_targets": self.effective_max_inflight_targets, "supervisor": asdict(self.supervisor)}

    @property
    def effective_max_inflight_targets(self) -> int:
        # One target can occupy each worker and reviewer slot concurrently.
        return self.max_inflight_targets or self.max_workers + self.max_reviewers

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ProjectControllerConfig":
        data = dict(value)
        data["supervisor"] = SupervisorConfig(**data.get("supervisor", {}))
        result = cls(**data)
        result.validate()
        return result


@dataclass(frozen=True)
class ProjectControllerResult:
    status: str
    run_id: str
    pause_reason: str | None = None
    detail: str | None = None
    integrated_tasks: int = 0
    usage: dict[str, Any] = field(default_factory=dict)


def validate_execution_policy(service, project, targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(targets, list):
        raise ValueError("execution_policy must be a list")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in targets:
        if not isinstance(raw, dict) or set(raw) != {"task_id", "wave", "worker_profile", "parallel_safe", "write_surfaces"}:
            raise ValueError("each execution-policy target must contain exactly task_id, wave, worker_profile, parallel_safe, and write_surfaces")
        task_id = raw["task_id"]
        if not isinstance(task_id, str) or not task_id or task_id in seen:
            raise ValueError("execution-policy task IDs must be non-empty and unique")
        task = service.con.execute(
            "SELECT state FROM tasks WHERE id=? AND project_id=?", (task_id, project["id"])
        ).fetchone()
        if not task or task["state"] not in {"PLANNED", "ASSIGNED", "SUBMITTED", "ACCEPTED", "COMPLETED"}:
            raise ValueError(f"execution-policy task {task_id} is unavailable")
        wave = raw["wave"]
        profile = raw["worker_profile"]
        surfaces = raw["write_surfaces"]
        if not isinstance(wave, int) or isinstance(wave, bool) or wave < 1:
            raise ValueError("execution-policy waves must be positive integers")
        if profile not in {"routine", "complex"}:
            raise ValueError("worker_profile must be routine or complex")
        if not isinstance(raw["parallel_safe"], bool):
            raise ValueError("parallel_safe must be boolean")
        if not isinstance(surfaces, list) or any(not isinstance(item, str) or not item for item in surfaces):
            raise ValueError("write_surfaces must be a list of non-empty strings")
        seen.add(task_id)
        normalized.append({
            "task_id": task_id,
            "wave": wave,
            "worker_profile": profile,
            "parallel_safe": raw["parallel_safe"],
            "write_surfaces": sorted(set(surfaces)),
        })
    planned = {
        row["id"]
        for row in service.con.execute(
            "SELECT id FROM tasks WHERE project_id=? AND state<>'CANCELLED'", (project["id"],)
        )
        if row["id"] not in {
            completed["id"]
            for completed in service.con.execute(
                "SELECT id FROM tasks WHERE project_id=? AND state='COMPLETED'", (project["id"],)
            )
        }
    }
    if seen != planned:
        raise ValueError(f"execution_policy must cover every unfinished task exactly once; missing={sorted(planned-seen)}, extra={sorted(seen-planned)}")
    return sorted(normalized, key=lambda item: (item["wave"], item["task_id"]))


class ProjectController:
    """Foreground deterministic scheduler for one approved Taskledger project plan."""

    GLOBAL_PAUSES = {
        PauseReason.BUDGET_EXHAUSTED,
        PauseReason.RUNTIME_UNCERTAIN,
        PauseReason.INTEGRATION_UNCERTAIN,
        PauseReason.CONFIGURATION_DRIFT,
        PauseReason.PLAN_INVALID,
    }

    def __init__(self, *, service, project, orchestrator, run_id: str, runtime, journal: Journal, config: ProjectControllerConfig):
        self.service = service
        self.project = project
        self.orchestrator = orchestrator
        self.run_id = run_id
        self.runtime = runtime
        self.journal = journal
        self.config = config
        self.config.validate()
        self.adapter = TaskledgerLedgerAdapter(service, project, orchestrator, worker_tool_enabled=True)
        self.worker_capacity = asyncio.Semaphore(config.max_workers)
        self.reviewer_capacity = asyncio.Semaphore(config.max_reviewers)
        self.brokers: dict[str, WorkerBroker] = {}

    async def run(self) -> ProjectControllerResult:
        with self.journal.project_lock(self.project["id"]):
            try:
                self._restore_worker_tools_for_recovery()
                probe = Supervisor(
                    project_id=self.project["id"], run_id=self.run_id, ledger=self.adapter, runtime=self.runtime,
                    journal=self.journal, config=self.config.supervisor, manage_run=False,
                )
                unresolved = await probe.reconcile_open_turns()
                if unresolved:
                    return self._pause(PauseReason.RUNTIME_UNCERTAIN, f"unresolved external turns: {', '.join(unresolved)}")
                probe.reconcile_resolved_reviewer_sessions()
                return await self._run_loop()
            except Exception as exc:
                self.journal.finish_run(self.run_id, "FAILED", reason=PauseReason.INVALID_STATE.value, detail=str(exc))
                raise

    async def _run_loop(self) -> ProjectControllerResult:
        while True:
            gate = self._global_gate()
            if gate:
                return self._pause(*gate)
            self._reconcile_targets()
            targets = self.journal.targets(self.run_id)
            remaining = [target for target in targets if target["state"] not in {"INTEGRATED", "CANCELLED"}]
            if not remaining:
                result = await self._finalize_project()
                if result is None:
                    continue
                return result
            wave = min(target["wave"] for target in remaining)
            runnable: list[dict[str, Any]] = []
            blocked: list[str] = []
            for target in remaining:
                if target["wave"] != wave:
                    self._target_event(target, "TARGET_WAITING_FOR_WAVE", "WAITING_FOR_WAVE")
                    continue
                if target["state"] == "BLOCKED":
                    blocked.append(target["task_id"])
                    self._target_event(target, "TARGET_BLOCKED", "BLOCKED_BY_TASKLEDGER")
                    continue
                task = self.service.con.execute("SELECT * FROM tasks WHERE id=?", (target["task_id"],)).fetchone()
                if task["state"] == "PLANNED":
                    allowed, reason = self.service.task_eligible(self.project, task)
                    if not allowed:
                        if reason in {"PLAN_INVALID", "RECOVERY_REQUIRED", "INITIAL_COMMIT_REQUIRED"}:
                            return self._pause(PauseReason.PLAN_INVALID if reason == "PLAN_INVALID" else PauseReason.RUNTIME_UNCERTAIN, reason)
                        if reason == "TASK_BLOCKED":
                            self.journal.set_target_state(self.run_id, target["task_id"], "BLOCKED")
                            blocked.append(target["task_id"])
                            self._target_event(target, "TARGET_BLOCKED", "BLOCKED_BY_TASKLEDGER")
                        elif reason == "DEPENDENCIES_UNSATISFIED":
                            self._target_event(target, "TARGET_WAITING_FOR_DEPENDENCY", "WAITING_FOR_DEPENDENCY")
                        continue
                runnable.append(target)
                self._target_event(target, "TARGET_BECAME_RUNNABLE", "RUNNABLE")
            batch = self._compatible_batch(runnable)
            selected = {target["task_id"] for target in batch}
            for target in runnable:
                if target["task_id"] not in selected:
                    reason = "WAITING_FOR_WRITE_SURFACE" if any(
                        self._write_surfaces_overlap(set(target["write_surfaces"]), set(item["write_surfaces"])) for item in batch
                    ) else "WAITING_FOR_WORKER_CAPACITY"
                    self._target_event(target, "TARGET_" + reason, reason)
            if not batch:
                return self._pause(PauseReason.BLOCKED, f"execution wave {wave} has no mechanically runnable targets; blocked={sorted(blocked)}")
            results = await asyncio.gather(*(self._run_target(target) for target in batch), return_exceptions=True)
            for target, result in zip(batch, results):
                if isinstance(result, Exception):
                    return self._pause(PauseReason.INVALID_STATE, f"target {target['task_id']} failed: {result}")
                if result.status == SupervisorStatus.INTEGRATED:
                    self.journal.set_target_state(self.run_id, target["task_id"], "INTEGRATED")
                elif result.pause_reason == PauseReason.ESCALATION_REQUIRED and self._escalate(target):
                    continue
                elif result.pause_reason in self.GLOBAL_PAUSES:
                    return self._pause(result.pause_reason, result.detail or result.pause_reason.value)
                else:
                    self.journal.set_target_state(self.run_id, target["task_id"], "BLOCKED")

    def _target_event(self, target: dict[str, Any], event_type: str, reason: str) -> None:
        self.journal.append_event(
            run_id=self.run_id, scope_type="TARGET", scope_id=target["task_id"],
            event_type=event_type, reason_code=reason, transition_only=True,
        )

    def _global_gate(self) -> tuple[PauseReason, str] | None:
        valid, _, _ = self.service.plan_current(self.project["id"])
        if not valid:
            return PauseReason.PLAN_INVALID, "approved Taskledger plan is no longer current"
        if self.service.ensure_operation_exists(self.project["id"]):
            return PauseReason.RUNTIME_UNCERTAIN, "Taskledger has an unresolved external operation"
        if self.service.con.execute(
            "SELECT 1 FROM blockers WHERE project_id=? AND scope_type='PROJECT' AND state='OPEN'", (self.project["id"],)
        ).fetchone():
            return PauseReason.BLOCKED, "a project-scoped blocker is open"
        if self.config.supervisor.max_total_tokens is not None:
            if self.journal.missing_usage_count(run_id=self.run_id):
                return PauseReason.BUDGET_EXHAUSTED, "token admission is unsafe because completed turn usage is missing"
            limit = self.config.supervisor.max_total_tokens + self.journal.granted_amount(run_id=self.run_id, kind="TOKENS")
            if self.journal.usage(run_id=self.run_id).total_tokens >= limit:
                return PauseReason.BUDGET_EXHAUSTED, "project token admission budget exhausted"
        elapsed = self.config.supervisor.max_elapsed_seconds + self.journal.granted_amount(run_id=self.run_id, kind="ELAPSED_SECONDS")
        if self.journal.elapsed_seconds(run_id=self.run_id) >= elapsed:
            return PauseReason.BUDGET_EXHAUSTED, "project elapsed-time budget exhausted"
        worker_limit = self.config.max_total_worker_turns + self.journal.granted_amount(run_id=self.run_id, kind="WORKER_TURNS")
        if self.journal.run_turn_count(run_id=self.run_id, roles=(SessionRole.WORKER.value,)) >= worker_limit:
            return PauseReason.BUDGET_EXHAUSTED, "project worker turn budget exhausted"
        reviewer_limit = self.config.max_total_reviewer_turns + self.journal.granted_amount(run_id=self.run_id, kind="REVIEWER_TURNS")
        reviewer_roles = (SessionRole.REVIEWER.value, SessionRole.REQUIREMENT_REVIEWER.value)
        if self.journal.run_turn_count(run_id=self.run_id, roles=reviewer_roles) >= reviewer_limit:
            return PauseReason.BUDGET_EXHAUSTED, "project reviewer turn budget exhausted"
        return None

    def _reconcile_targets(self) -> None:
        for target in self.journal.targets(self.run_id):
            task = self.service.con.execute("SELECT * FROM tasks WHERE id=?", (target["task_id"],)).fetchone()
            if task["state"] == "COMPLETED" and self.service.current_integration(self.project, task["id"]):
                self.journal.set_target_state(self.run_id, task["id"], "INTEGRATED")
                self._close_target_worker(target)
            elif task["state"] == "CANCELLED":
                self.journal.set_target_state(self.run_id, task["id"], "CANCELLED")
                self._close_target_worker(target)
            elif target["assignment_id"] and task["state"] == "PLANNED":
                assignment = self.service.con.execute(
                    "SELECT state,revocation_reason FROM assignments WHERE id=?", (target["assignment_id"],)
                ).fetchone()
                if assignment and assignment["state"] in {"REVOKED", "CLOSED"}:
                    self._close_target_worker(target)
                    profile = "complex" if (
                        target["worker_profile"] == "routine"
                        and assignment["revocation_reason"] == "routine rejection limit reached"
                    ) else target["worker_profile"]
                    self.journal.reroute_target(self.run_id, task["id"], profile=profile)
            elif target["state"] == "BLOCKED" and not self.service.blocking_reasons(self.project["id"], "assign", task_id=task["id"], assignment_id=target["assignment_id"]):
                self.journal.set_target_state(self.run_id, task["id"], "ACTIVE" if target["assignment_id"] else "QUEUED")

    def _close_target_worker(self, target: dict[str, Any]) -> None:
        if not target.get("assignment_id"):
            return
        session = self.journal.find_active_session(
            project_id=self.project["id"], role=SessionRole.WORKER.value, subject_id=target["assignment_id"]
        )
        if session:
            self.journal.close_session(session.id)

    def _restore_worker_tools_for_recovery(self) -> None:
        for target in self.journal.targets(self.run_id):
            assignment_id = target["assignment_id"]
            if not assignment_id:
                continue
            assignment = self.service.con.execute(
                "SELECT state FROM assignments WHERE id=? AND project_id=?", (assignment_id, self.project["id"])
            ).fetchone()
            if assignment and assignment["state"] == "ACTIVE":
                self._install_worker_tool(assignment_id)

    def _compatible_batch(self, targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
        chosen: list[dict[str, Any]] = []
        surfaces: set[str] = set()
        limit = self.config.effective_max_inflight_targets
        for target in sorted(targets, key=lambda item: item["task_id"]):
            own = set(target["write_surfaces"])
            if chosen and (not target["parallel_safe"] or any(not item["parallel_safe"] for item in chosen)):
                continue
            if self._write_surfaces_overlap(surfaces, own):
                continue
            chosen.append(target)
            surfaces.update(own)
            if len(chosen) >= limit or not target["parallel_safe"]:
                break
        return chosen

    @staticmethod
    def _write_surfaces_overlap(left: set[str], right: set[str]) -> bool:
        for first in left:
            normalized_first = first.rstrip("/")
            for second in right:
                normalized_second = second.rstrip("/")
                if (
                    normalized_first == normalized_second
                    or normalized_first.startswith(normalized_second + "/")
                    or normalized_second.startswith(normalized_first + "/")
                ):
                    return True
        return False

    async def _run_target(self, target: dict[str, Any]):
        assignment_id = self._ensure_assignment(target)
        self._install_worker_tool(assignment_id)
        supervisor = Supervisor(
            project_id=self.project["id"], run_id=self.run_id, ledger=self.adapter, runtime=self.runtime,
            journal=self.journal, config=self.config.supervisor, worker_capacity=self.worker_capacity,
            reviewer_capacity=self.reviewer_capacity, run_worker_turn_limit=self.config.max_total_worker_turns,
            run_reviewer_turn_limit=self.config.max_total_reviewer_turns, worker_token_reserve=self.config.reviewer_token_reserve,
            manage_run=False, reconcile_runtime=False,
        )
        return await supervisor.run_assignment(assignment_id)

    def _ensure_assignment(self, target: dict[str, Any]) -> str:
        if target["assignment_id"]:
            row = self.service.con.execute("SELECT state FROM assignments WHERE id=?", (target["assignment_id"],)).fetchone()
            if row and row["state"] in {"ACTIVE", "CLOSED"}:
                return target["assignment_id"]
        existing = self.service.con.execute(
            "SELECT id,worker_profile FROM assignments WHERE task_id=? AND state='ACTIVE' ORDER BY attempt_number DESC LIMIT 1",
            (target["task_id"],),
        ).fetchone()
        if existing:
            if existing["worker_profile"] != target["worker_profile"]:
                raise ConfigurationDrift("active assignment profile differs from approved execution policy")
            self.journal.activate_target(self.run_id, target["task_id"], existing["id"])
            return existing["id"]
        created = self.service.assignment_create(
            self.project, self.orchestrator, {"task_id": target["task_id"], "worker_profile": target["worker_profile"]}
        )
        self.journal.activate_target(self.run_id, target["task_id"], created["assignment_id"])
        return created["assignment_id"]

    def _install_worker_tool(self, assignment_id: str) -> None:
        if assignment_id in self.brokers:
            return
        token_path = self.service.home / "projects" / self.project["id"] / "assignments" / assignment_id / "worker-token"
        worker = self.service.authenticate(self.project["id"], token_path.read_text().strip(), "WORKER")
        broker = WorkerBroker(self.service, worker, token_path.parent / "unused.sock")
        self.brokers[assignment_id] = broker
        self.runtime.worker_tools[assignment_id] = broker.dispatch

    def _escalate(self, target: dict[str, Any]) -> bool:
        task = self.service.con.execute("SELECT state FROM tasks WHERE id=?", (target["task_id"],)).fetchone()
        if target["worker_profile"] != "routine" or not task or task["state"] != "PLANNED":
            return False
        if target["assignment_id"]:
            session = self.journal.find_active_session(
                project_id=self.project["id"], role=SessionRole.WORKER.value, subject_id=target["assignment_id"]
            )
            if session:
                self.journal.close_session(session.id)
        self.journal.reroute_target(self.run_id, target["task_id"], profile="complex")
        return True

    async def _finalize_project(self) -> ProjectControllerResult | None:
        valid, fingerprint, _ = self.service.plan_current(self.project["id"])
        if not valid or not fingerprint:
            return self._pause(PauseReason.PLAN_INVALID, "plan changed before final integrated review")
        oid = git.oid(self.project["repository_root"], f"refs/heads/{self.project['canonical_branch']}")
        requirements = self.service.requirement_rows(self.project)
        review = self.journal.final_review(run_id=self.run_id, canonical_oid=oid, plan_fingerprint=fingerprint)
        review_id = review["id"] if review else self.journal.create_final_review(run_id=self.run_id, canonical_oid=oid, plan_fingerprint=fingerprint)
        if not review or review["state"] == "PENDING":
            try:
                payload, turn_id = await self._semantic_job(
                    role=SessionRole.REQUIREMENT_REVIEWER,
                    profile=self.config.supervisor.reviewer_profile,
                    subject_id=review_id,
                    prompt=self._final_review_prompt(oid, fingerprint, requirements),
                    schema=self._final_review_schema(requirements),
                    max_turns=self.config.max_final_reviewer_turns,
                    prompt_kind="final-review",
                    validator=lambda value: self._validate_final_verdict(value, requirements),
                )
            except ConfigurationDrift as exc:
                return self._pause(PauseReason.CONFIGURATION_DRIFT, str(exc))
            except SemanticJobBudgetExhausted as exc:
                return self._pause(PauseReason.BUDGET_EXHAUSTED, str(exc))
            except SemanticJobInvalidOutput as exc:
                return self._pause(PauseReason.REVIEWER_FAILURE, str(exc))
            except SemanticJobUncertain as exc:
                return self._pause(PauseReason.RUNTIME_UNCERTAIN, str(exc))
            verdict = payload
            current_valid, current_fingerprint, _ = self.service.plan_current(self.project["id"])
            current_oid = git.oid(self.project["repository_root"], f"refs/heads/{self.project['canonical_branch']}")
            if not current_valid or current_fingerprint != fingerprint or current_oid != oid:
                return self._pause(PauseReason.PLAN_INVALID, "canonical state or plan changed during final review")
            state = verdict["outcome"]
            self.journal.resolve_final_review(review_id, state=state, verdict=verdict)
            self.journal.consume_turn(turn_id)
            self._close_session(SessionRole.REQUIREMENT_REVIEWER, review_id)
        else:
            verdict = review["verdict"]
            state = review["state"]
        if state == "AMBIGUOUS":
            return self._pause(PauseReason.BLOCKED, "final review found product ambiguity requiring user input")
        if state == "DEFECTS":
            return await self._create_corrections(review_id, verdict)
        try:
            for result in verdict["requirement_results"]:
                self.service.requirement_verify(
                    self.project, self.orchestrator,
                    {"requirement_id": result["requirement_id"], "evidence": [{"label": "final integrated review", "details": result["evidence"]}], "notes": "Verified by bounded final integrated review."},
                )
            self.service.complete_project(self.project, self.orchestrator)
        except LedgerError as exc:
            return self._pause(PauseReason.PROJECT_INCOMPLETE, f"{exc.code}: {exc.message}")
        self.journal.finish_run(self.run_id, "COMPLETED")
        return ProjectControllerResult("COMPLETED", self.run_id, integrated_tasks=sum(t["state"] == "INTEGRATED" for t in self.journal.targets(self.run_id)), usage=self.journal.usage_report(run_id=self.run_id))

    async def _create_corrections(self, review_id: str, verdict: dict[str, Any]) -> ProjectControllerResult | None:
        try:
            payload, turn_id = await self._semantic_job(
                role=SessionRole.TASK_CREATOR,
                profile=self.config.task_creator_profile,
                subject_id=review_id,
                prompt="Create the smallest bounded correction tasks for these final-review implementation defects. Do not change product behavior. Return only structured tasks.\n\n" + canonical(self._correction_projection(verdict)),
                schema=self._correction_schema(),
                max_turns=self.config.max_task_creator_turns,
                prompt_kind="correction-planning",
                validator=lambda value: self._validate_corrections(value, review_id),
            )
        except ConfigurationDrift as exc:
            return self._pause(PauseReason.CONFIGURATION_DRIFT, str(exc))
        except SemanticJobBudgetExhausted as exc:
            return self._pause(PauseReason.BUDGET_EXHAUSTED, str(exc))
        except SemanticJobInvalidOutput as exc:
            return self._pause(PauseReason.REVIEWER_FAILURE, str(exc))
        except SemanticJobUncertain as exc:
            return self._pause(PauseReason.RUNTIME_UNCERTAIN, str(exc))
        tasks = payload
        refs = [task["ref"] for task in tasks]
        existing = self._task_ids_for_plan_refs(refs)
        if existing and set(existing) != set(refs):
            return self._pause(PauseReason.RUNTIME_UNCERTAIN, "correction-plan materialization is only partially provable")
        if not existing:
            result = self.service.apply_plan(self.project, self.orchestrator, {"requirements": [], "tasks": [self._plan_task(task) for task in tasks]})
            task_ids = result["task_ids"]
        else:
            task_ids = existing
        validated = self.service.validate_plan(self.project, self.orchestrator)
        if not validated["valid"]:
            return self._pause(PauseReason.PLAN_INVALID, "task creator correction plan failed Taskledger validation")
        wave = max((target["wave"] for target in self.journal.targets(self.run_id)), default=0) + 1
        self.journal.add_targets(self.run_id, [{
            "task_id": task_ids[task["ref"]], "wave": wave, "worker_profile": task["worker_profile"],
            "parallel_safe": task["parallel_safe"], "write_surfaces": task["write_surfaces"],
        } for task in tasks])
        self.journal.consume_turn(turn_id)
        self._close_session(SessionRole.TASK_CREATOR, review_id)
        return None

    async def _semantic_job(self, *, role: SessionRole, profile: str, subject_id: str, prompt: str, schema: dict[str, Any], max_turns: int, prompt_kind: str, validator) -> tuple[Any, str]:
        cwd = self.project["repository_root"]
        identity = self.runtime.session_identity(role=role, profile=profile, subject_id=subject_id, cwd=cwd, writable=False)
        identity_dict = identity.as_dict()
        config_hash = sha256(canonical(identity_dict))
        session = self.journal.find_active_session(project_id=self.project["id"], role=role.value, subject_id=subject_id)
        if session:
            if session.config_hash != config_hash or session.runtime_identity != identity_dict:
                raise ConfigurationDrift("persisted semantic-job runtime configuration differs from the resolved profile")
            await self.runtime.resume_session(
                thread_id=session.thread_id, role=role, profile=profile, subject_id=subject_id,
                cwd=cwd, writable=False,
                cumulative_usage_baseline=self.journal.latest_cumulative_usage(session.id),
            )
        else:
            runtime_session = await self.runtime.start_session(role=role, profile=profile, subject_id=subject_id, cwd=cwd, writable=False)
            if runtime_session.identity and runtime_session.identity != identity:
                raise ConfigurationDrift("runtime started a semantic job with an unexpected configuration")
            session = self.journal.create_session(
                run_id=self.run_id, project_id=self.project["id"], role=role.value, profile=profile,
                subject_id=subject_id, external_thread_id=runtime_session.thread_id, config_hash=config_hash,
                runtime_identity=identity_dict,
            )
        original_prompt = prompt
        while self.journal.session_turn_count(session.id) < max_turns:
            terminal = self.journal.terminal_unconsumed_turn(session.id)
            if terminal is None:
                if role == SessionRole.REQUIREMENT_REVIEWER:
                    limit = self.config.max_total_reviewer_turns + self.journal.granted_amount(run_id=self.run_id, kind="REVIEWER_TURNS")
                    reviewer_roles = (SessionRole.REVIEWER.value, SessionRole.REQUIREMENT_REVIEWER.value)
                    if self.journal.run_turn_count(run_id=self.run_id, roles=reviewer_roles) >= limit:
                        raise SemanticJobBudgetExhausted("project reviewer turn budget exhausted")
                if role == SessionRole.TASK_CREATOR:
                    limit = self.config.max_total_task_creator_turns + self.journal.granted_amount(
                        run_id=self.run_id, kind="TASK_CREATOR_TURNS"
                    )
                    if self.journal.run_turn_count(run_id=self.run_id, roles=(SessionRole.TASK_CREATOR.value,)) >= limit:
                        raise SemanticJobBudgetExhausted("project task creator turn budget exhausted")
                if self.config.supervisor.max_total_tokens is not None:
                    if self.journal.missing_usage_count(run_id=self.run_id):
                        raise SemanticJobBudgetExhausted("token admission is unsafe because completed turn usage is missing")
                    token_limit = self.config.supervisor.max_total_tokens + self.journal.granted_amount(
                        run_id=self.run_id, kind="TOKENS"
                    )
                    if self.journal.usage(run_id=self.run_id).total_tokens >= token_limit:
                        raise SemanticJobBudgetExhausted("project token admission budget exhausted")
                elapsed_limit = self.config.supervisor.max_elapsed_seconds + self.journal.granted_amount(
                    run_id=self.run_id, kind="ELAPSED_SECONDS"
                )
                if self.journal.elapsed_seconds(run_id=self.run_id) >= elapsed_limit:
                    raise SemanticJobBudgetExhausted("project elapsed-time budget exhausted")
                retry = self.journal.session_completed_turn_count(session.id) > 0
                turn_prompt = (
                    "Your previous result did not satisfy the required output schema. Return a corrected structured result only. Do not redo the review or planning unless necessary."
                    if retry else original_prompt
                )
                reason = DispatchReason.STRUCTURED_OUTPUT_RETRY if retry else (
                    DispatchReason.FINAL_REVIEW if role == SessionRole.REQUIREMENT_REVIEWER else DispatchReason.CORRECTION_PLANNING
                )
                packet = PromptPacket(
                    text=turn_prompt, dispatch_reason=reason,
                    controller_payload_bytes=len(turn_prompt.encode("utf-8")),
                    output_schema_bytes=len(canonical(schema).encode("utf-8")),
                )
                local_id = self.journal.begin_turn(session.id, prompt_kind, packet=packet)
                handle = None
                try:
                    async with self.reviewer_capacity:
                        handle = await self.runtime.start_turn(thread_id=session.thread_id, prompt=turn_prompt, output_schema=schema)
                        self.journal.acknowledge_turn(local_id, handle)
                        result = await asyncio.wait_for(self.runtime.wait_turn(handle), timeout=self.config.supervisor.turn_timeout_seconds)
                    self.journal.record_usage_events(local_id, self.runtime.usage_events(handle))
                    self.journal.complete_turn(local_id, result)
                except Exception as exc:
                    inspection = await self.runtime.inspect_turn(handle) if handle is not None else None
                    if inspection and inspection.state == "FAILED":
                        self.journal.fail_turn(local_id, inspection.error or str(exc), uncertain=False, result=inspection.result)
                        self.journal.consume_turn(local_id)
                        continue
                    self.journal.fail_turn(local_id, str(exc), uncertain=True)
                    raise SemanticJobUncertain("semantic job outcome is uncertain") from exc
                terminal = self.journal.terminal_unconsumed_turn(session.id)
            if terminal and terminal.state == "COMPLETED":
                value = validator(terminal.result.structured_output if terminal.result else None)
                if value is not None:
                    return value, terminal.id
                self.journal.consume_turn(terminal.id)
                continue
            if terminal:
                self.journal.consume_turn(terminal.id)
        raise SemanticJobInvalidOutput("semantic job did not produce valid structured output within its turn budget")

    def _final_review_prompt(self, oid: str, fingerprint: str, requirements: list[dict[str, Any]]) -> str:
        return "Independently review the complete integrated canonical repository against every active requirement and registered specification. Inspect cross-task seams and actual code. Classify implementation defects separately from genuine product ambiguity. Return every requirement exactly once.\n\n" + canonical(self._final_review_projection(oid, fingerprint, requirements))

    def _final_review_projection(self, oid: str, fingerprint: str, requirements: list[dict[str, Any]]) -> dict[str, Any]:
        plan = self.service.plan_object(self.project["id"])
        tasks = {task["id"]: task for task in plan["tasks"]}
        projected_requirements = []
        mapping: list[dict[str, Any]] = []
        for requirement in plan["requirements"]:
            covering = sorted(task["id"] for task in tasks.values() if requirement["id"] in task["requirements"] and task["state"] != "CANCELLED")
            integrated = []
            for task_id in covering:
                attempt = self.service.current_integration(self.project, task_id)
                if attempt:
                    integrated.append({"task_id": task_id, "canonical_oid": attempt["canonical_after_oid"]})
                mapping.append({"task_id": task_id, "requirement_id": requirement["id"]})
            projected_requirements.append({
                "id": requirement["id"], "statement": requirement["statement"], "details": requirement["details"],
                "implementation_required": requirement["implementation_required"], "sources": requirement["sources"],
                "covering_task_ids": covering, "integrated_tasks": integrated,
            })
        return {
            "canonical_oid": oid, "plan_fingerprint": fingerprint,
            "requirements": projected_requirements,
            "task_requirement_mapping": sorted(mapping, key=lambda item: (item["task_id"], item["requirement_id"])),
            "specifications": [
                {"id": item["id"], "relative_path": item["relative_path"], "content_hash": item["content_hash"]}
                for item in plan["specifications"] if item["lifecycle"] == "ACTIVE"
            ],
        }

    def _correction_projection(self, verdict: dict[str, Any]) -> dict[str, Any]:
        affected = sorted({rid for finding in verdict["findings"] for rid in finding["requirement_ids"]})
        plan = self.service.plan_object(self.project["id"])
        all_tasks = {task["id"]: task for task in plan["tasks"]}
        affected_task_ids = {
            task["id"] for task in plan["tasks"]
            if set(task["requirements"]).intersection(affected)
        }
        dependency_ids = {
            dependency
            for task_id in affected_task_ids
            for dependency in all_tasks[task_id]["dependencies"]
        }
        relevant_task_ids = affected_task_ids | dependency_ids
        tasks = [
            {
                "id": task["id"], "state": task["state"],
                "requirements": task["requirements"], "dependencies": task["dependencies"],
                "relationship": "AFFECTED" if task["id"] in affected_task_ids else "DEPENDENCY",
            }
            for task in plan["tasks"] if task["id"] in relevant_task_ids
        ]
        policy = [
            {key: target[key] for key in ("task_id", "wave", "worker_profile", "parallel_safe", "write_surfaces")}
            for target in self.journal.targets(self.run_id) if target["task_id"] in relevant_task_ids
        ]
        return {
            "findings": verdict["findings"],
            "affected_requirements": [req for req in plan["requirements"] if req["id"] in affected],
            "relevant_tasks": tasks,
            "execution_policy": policy,
            "active_specifications": [
                {"id": spec["id"], "relative_path": spec["relative_path"], "content_hash": spec["content_hash"]}
                for spec in plan["specifications"] if spec["lifecycle"] == "ACTIVE"
            ],
        }

    @staticmethod
    def _final_review_schema(requirements: list[dict[str, Any]]) -> dict[str, Any]:
        ids = [item["id"] for item in requirements]
        requirement_result = {
            "type": "object",
            "properties": {
                "requirement_id": {"type": "string", "enum": ids},
                "satisfied": {"type": "boolean"},
                "evidence": {"type": "string", "minLength": 1},
            },
            "required": ["requirement_id", "satisfied", "evidence"],
            "additionalProperties": False,
        }
        finding = {
            "type": "object",
            "properties": {
                "classification": {"type": "string", "enum": ["IMPLEMENTATION_DEFECT", "PRODUCT_AMBIGUITY"]},
                "description": {"type": "string", "minLength": 1},
                "requirement_ids": {"type": "array", "items": {"type": "string", "enum": ids}},
            },
            "required": ["classification", "description", "requirement_ids"],
            "additionalProperties": False,
        }
        return {
            "type": "object",
            "properties": {
                "outcome": {"type": "string", "enum": ["SATISFIED", "DEFECTS", "AMBIGUOUS"]},
                "requirement_results": {"type": "array", "minItems": len(ids), "maxItems": len(ids), "items": requirement_result},
                "findings": {"type": "array", "items": finding},
                "notes": {"type": "string"},
            },
            "required": ["outcome", "requirement_results", "findings", "notes"],
            "additionalProperties": False,
        }

    @staticmethod
    def _validate_final_verdict(payload: dict[str, Any] | None, requirements: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not isinstance(payload, dict) or set(payload) != {"outcome", "requirement_results", "findings", "notes"}:
            return None
        expected = {item["id"] for item in requirements}
        results = payload.get("requirement_results")
        findings = payload.get("findings")
        if not isinstance(results, list) or {item.get("requirement_id") for item in results if isinstance(item, dict)} != expected or len(results) != len(expected):
            return None
        if any(set(item) != {"requirement_id", "satisfied", "evidence"} or not isinstance(item["satisfied"], bool) or not isinstance(item["evidence"], str) or not item["evidence"] for item in results):
            return None
        if payload.get("outcome") not in {"SATISFIED", "DEFECTS", "AMBIGUOUS"} or not isinstance(findings, list) or not isinstance(payload.get("notes"), str):
            return None
        for item in findings:
            if (
                not isinstance(item, dict)
                or set(item) != {"classification", "description", "requirement_ids"}
                or item.get("classification") not in {"IMPLEMENTATION_DEFECT", "PRODUCT_AMBIGUITY"}
                or not isinstance(item.get("description"), str)
                or not item["description"]
                or not isinstance(item.get("requirement_ids"), list)
                or any(not isinstance(requirement_id, str) or requirement_id not in expected for requirement_id in item["requirement_ids"])
                or len(set(item["requirement_ids"])) != len(item["requirement_ids"])
            ):
                return None
        classifications = {item["classification"] for item in findings}
        if payload["outcome"] == "SATISFIED" and (findings or not all(item["satisfied"] for item in results)):
            return None
        if payload["outcome"] == "DEFECTS" and (all(item["satisfied"] for item in results) or "IMPLEMENTATION_DEFECT" not in classifications or "PRODUCT_AMBIGUITY" in classifications):
            return None
        if payload["outcome"] == "AMBIGUOUS" and (all(item["satisfied"] for item in results) or "PRODUCT_AMBIGUITY" not in classifications):
            return None
        return payload

    @staticmethod
    def _correction_schema() -> dict[str, Any]:
        strings = {"type": "array", "items": {"type": "string", "minLength": 1}}
        task = {
            "type": "object",
            "properties": {
                "objective": {"type": "string", "minLength": 1},
                "implementation_scope": {"type": "string", "minLength": 1},
                "acceptance_criteria": {**strings, "minItems": 1},
                "required_checks": strings,
                "requirement_ids": {**strings, "minItems": 1},
                "dependency_task_ids": strings,
                "worker_profile": {"type": "string", "enum": ["routine", "complex"]},
                "parallel_safe": {"type": "boolean"},
                "write_surfaces": strings,
            },
            "required": ["objective", "implementation_scope", "acceptance_criteria", "required_checks", "requirement_ids", "dependency_task_ids", "worker_profile", "parallel_safe", "write_surfaces"],
            "additionalProperties": False,
        }
        return {
            "type": "object",
            "properties": {"tasks": {"type": "array", "minItems": 1, "items": task}},
            "required": ["tasks"],
            "additionalProperties": False,
        }

    def _validate_corrections(self, payload: dict[str, Any] | None, review_id: str) -> list[dict[str, Any]] | None:
        if not isinstance(payload, dict) or set(payload) != {"tasks"} or not isinstance(payload["tasks"], list) or not payload["tasks"]:
            return None
        active_requirements = {row["id"] for row in self.service.con.execute("SELECT id FROM requirements WHERE project_id=? AND lifecycle='ACTIVE'", (self.project["id"],))}
        project_tasks = {row["id"] for row in self.service.con.execute("SELECT id FROM tasks WHERE project_id=? AND state<>'CANCELLED'", (self.project["id"],))}
        fields = {
            "objective", "implementation_scope", "acceptance_criteria", "required_checks", "requirement_ids",
            "dependency_task_ids", "worker_profile", "parallel_safe", "write_surfaces",
        }
        result = []
        for index, task in enumerate(payload["tasks"], 1):
            if not isinstance(task, dict) or set(task) != fields:
                return None
            string_fields = ("objective", "implementation_scope")
            list_fields = ("acceptance_criteria", "required_checks", "requirement_ids", "dependency_task_ids", "write_surfaces")
            if any(not isinstance(task[name], str) or not task[name] for name in string_fields):
                return None
            if any(not isinstance(task[name], list) or any(not isinstance(item, str) or not item for item in task[name]) for name in list_fields):
                return None
            if (
                not task["acceptance_criteria"]
                or not task["requirement_ids"]
                or not set(task["requirement_ids"]).issubset(active_requirements)
                or not set(task["dependency_task_ids"]).issubset(project_tasks)
                or task["worker_profile"] not in {"routine", "complex"}
                or not isinstance(task["parallel_safe"], bool)
            ):
                return None
            result.append({
                **task,
                "acceptance_criteria": list(dict.fromkeys(task["acceptance_criteria"])),
                "required_checks": list(dict.fromkeys(task["required_checks"])),
                "requirement_ids": list(dict.fromkeys(task["requirement_ids"])),
                "dependency_task_ids": list(dict.fromkeys(task["dependency_task_ids"])),
                "write_surfaces": sorted(set(task["write_surfaces"])),
                "ref": f"controller-correction-{review_id}-{index}",
            })
        return result

    @staticmethod
    def _plan_task(task: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in task.items() if key not in {"worker_profile", "parallel_safe", "write_surfaces"}} | {"requirement_refs": [], "dependency_refs": []}

    def _task_ids_for_plan_refs(self, refs: list[str]) -> dict[str, str]:
        found: dict[str, str] = {}
        for row in self.service.con.execute("SELECT entity_id,payload_json FROM audit_events WHERE project_id=? AND event_type='TASK_CREATED'", (self.project["id"],)):
            payload = json.loads(row["payload_json"])
            if payload.get("plan_ref") in refs:
                found[payload["plan_ref"]] = row["entity_id"]
        return found

    def _close_session(self, role: SessionRole, subject_id: str) -> None:
        session = self.journal.find_active_session(project_id=self.project["id"], role=role.value, subject_id=subject_id)
        if session:
            self.journal.close_session(session.id)

    def _pause(self, reason: PauseReason, detail: str) -> ProjectControllerResult:
        self.journal.finish_run(self.run_id, "PAUSED", reason=reason.value, detail=detail)
        return ProjectControllerResult("PAUSED", self.run_id, reason.value, detail, sum(t["state"] == "INTEGRATED" for t in self.journal.targets(self.run_id)), self.journal.usage_report(run_id=self.run_id))
