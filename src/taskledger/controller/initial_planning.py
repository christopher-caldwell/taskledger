from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

from taskledger.core import LedgerError, canonical, new_id, now, require_object, sha256, text
from taskledger.db import transaction

from .journal import Journal
from .model import DispatchReason, PromptPacket, SessionRole


@dataclass(frozen=True)
class PreparationConfig:
    max_initial_planner_turns: int
    execution_limits: dict[str, Any]
    local_inputs: list[dict[str, Any]]
    services: list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "max_initial_planner_turns": self.max_initial_planner_turns,
            "execution_limits": self.execution_limits,
            "preflight": {"local_inputs": self.local_inputs, "services": self.services},
        }


def parse_preparation_request(data: dict[str, Any]) -> tuple[str, PreparationConfig, str | None]:
    require_object(data, {"spec_path", "live", "limits", "preflight", "run_group_id"}, {"spec_path", "live"})
    if data["live"] is not True:
        raise LedgerError("INVALID_REQUEST", "Live initial planning requires live=true explicit opt in.")
    limits = data.get("limits", {})
    require_object(limits, {
        "max_initial_planner_turns", "max_total_worker_turns", "max_total_reviewer_turns",
        "max_total_task_creator_turns", "max_worker_turns_per_assignment",
        "max_reviewer_turns_per_submission", "max_final_reviewer_turns", "max_total_tokens",
        "turn_timeout_seconds", "max_elapsed_seconds", "max_workers", "max_reviewers",
        "max_inflight_targets", "reviewer_token_reserve",
    })
    numeric_defaults = {
        "max_initial_planner_turns": 2, "max_total_worker_turns": 100,
        "max_total_reviewer_turns": 50, "max_total_task_creator_turns": 6,
        "max_worker_turns_per_assignment": 20, "max_reviewer_turns_per_submission": 2,
        "max_final_reviewer_turns": 2, "max_total_tokens": 40000,
        "turn_timeout_seconds": 1800, "max_elapsed_seconds": 3600,
        "max_workers": 2, "max_reviewers": 1, "reviewer_token_reserve": 0,
    }
    values = {name: limits.get(name, default) for name, default in numeric_defaults.items()}
    if any(not isinstance(value, int) or isinstance(value, bool) for value in values.values()):
        raise LedgerError("INVALID_REQUEST", "Preparation limits must be integers.")
    if any(values[name] <= 0 for name in values if name != "reviewer_token_reserve") or values["reviewer_token_reserve"] < 0:
        raise LedgerError("INVALID_REQUEST", "Preparation limits must be positive; reviewer_token_reserve may be zero.")
    if values["reviewer_token_reserve"] > values["max_total_tokens"]:
        raise LedgerError("INVALID_REQUEST", "reviewer_token_reserve must not exceed max_total_tokens.")
    max_inflight = limits.get("max_inflight_targets")
    if max_inflight is not None and (not isinstance(max_inflight, int) or isinstance(max_inflight, bool) or max_inflight <= 0):
        raise LedgerError("INVALID_REQUEST", "max_inflight_targets must be a positive integer.")
    execution_limits = {
        "max_workers": values["max_workers"], "max_reviewers": values["max_reviewers"],
        "max_total_worker_turns": values["max_total_worker_turns"],
        "max_total_reviewer_turns": values["max_total_reviewer_turns"],
        "max_total_task_creator_turns": values["max_total_task_creator_turns"],
        "reviewer_token_reserve": values["reviewer_token_reserve"],
        "max_final_reviewer_turns": values["max_final_reviewer_turns"],
        "max_task_creator_turns": values["max_initial_planner_turns"],
        "max_inflight_targets": max_inflight,
        "task_creator_profile": "taskledger_task_creator",
        "supervisor": {
            "max_worker_turns": values["max_worker_turns_per_assignment"],
            "max_consecutive_stalled_turns": 2, "max_consecutive_runtime_failures": 2,
            "max_reviewer_turns_per_submission": values["max_reviewer_turns_per_submission"],
            "max_total_tokens": values["max_total_tokens"],
            "max_elapsed_seconds": values["max_elapsed_seconds"],
            "turn_timeout_seconds": values["turn_timeout_seconds"],
            "reviewer_profile": "taskledger_reviewer",
        },
    }
    preflight = data.get("preflight", {})
    require_object(preflight, {"local_inputs", "services"})
    local_inputs = preflight.get("local_inputs", [])
    services = preflight.get("services", [])
    if not isinstance(local_inputs, list) or not isinstance(services, list):
        raise LedgerError("INVALID_REQUEST", "preflight local_inputs and services must be arrays.")
    run_group_id = data.get("run_group_id")
    if run_group_id is not None:
        run_group_id = text(run_group_id, "run_group_id")
    return text(data["spec_path"], "spec_path"), PreparationConfig(
        values["max_initial_planner_turns"], execution_limits, local_inputs, services
    ), run_group_id


def proposal_schema() -> dict[str, Any]:
    strings = {"type": "array", "items": {"type": "string", "minLength": 1}}
    source = {
        "type": "object",
        "properties": {"locator": {"type": "string", "minLength": 1}, "excerpt": {"type": ["string", "null"]}},
        "required": ["locator", "excerpt"], "additionalProperties": False,
        "description": "Use each locator once per requirement. If several excerpts support it, combine distinct excerpts with two newlines.",
    }
    requirement = {
        "type": "object",
        "properties": {
            "ref": {"type": "string", "minLength": 1}, "statement": {"type": "string", "minLength": 1},
            "details": {"type": "string", "minLength": 1}, "implementation_required": {"type": "boolean"},
            "sources": {"type": "array", "minItems": 1, "items": source, "description": "Canonical source citations, one entry per locator."},
        },
        "required": ["ref", "statement", "details", "implementation_required", "sources"], "additionalProperties": False,
    }
    task = {
        "type": "object",
        "properties": {
            "ref": {"type": "string", "minLength": 1}, "objective": {"type": "string", "minLength": 1},
            "implementation_scope": {"type": "string", "minLength": 1},
            "acceptance_criteria": {**strings, "minItems": 1}, "required_checks": strings,
            "requirement_refs": {**strings, "minItems": 1}, "dependency_refs": strings,
        },
        "required": ["ref", "objective", "implementation_scope", "acceptance_criteria", "required_checks", "requirement_refs", "dependency_refs"],
        "additionalProperties": False,
    }
    policy = {
        "type": "object",
        "properties": {
            "task_ref": {"type": "string", "minLength": 1}, "wave": {"type": "integer", "minimum": 1},
            "worker_profile": {"type": "string", "enum": ["routine", "complex"]},
            "parallel_safe": {"type": "boolean"}, "write_surfaces": {**strings, "minItems": 1},
        },
        "required": ["task_ref", "wave", "worker_profile", "parallel_safe", "write_surfaces"], "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "requirements": {"type": "array", "items": requirement},
            "tasks": {"type": "array", "items": task},
            "execution_policy": {"type": "array", "items": policy},
            "ambiguities": strings, "assumptions": strings,
        },
        "required": ["requirements", "tasks", "execution_policy", "ambiguities", "assumptions"],
        "additionalProperties": False,
    }


def validate_proposal(value: Any) -> dict[str, Any] | None:
    fields = {"requirements", "tasks", "execution_policy", "ambiguities", "assumptions"}
    if not isinstance(value, dict) or set(value) != fields:
        return None
    if any(not isinstance(value[name], list) for name in fields):
        return None
    if any(not isinstance(item, str) or not item for name in ("ambiguities", "assumptions") for item in value[name]):
        return None
    requirement_fields = {"ref", "statement", "details", "implementation_required", "sources"}
    task_fields = {"ref", "objective", "implementation_scope", "acceptance_criteria", "required_checks", "requirement_refs", "dependency_refs"}
    policy_fields = {"task_ref", "wave", "worker_profile", "parallel_safe", "write_surfaces"}
    req_refs: set[str] = set()
    for item in value["requirements"]:
        if not isinstance(item, dict) or set(item) != requirement_fields:
            return None
        if not all(isinstance(item[name], str) and item[name] for name in ("ref", "statement", "details")):
            return None
        if item["ref"] in req_refs or not isinstance(item["implementation_required"], bool) or not isinstance(item["sources"], list) or not item["sources"]:
            return None
        if any(not isinstance(source, dict) or set(source) != {"locator", "excerpt"} or not isinstance(source["locator"], str) or not source["locator"] or source["excerpt"] is not None and not isinstance(source["excerpt"], str) for source in item["sources"]):
            return None
        item["sources"] = normalize_proposal_sources(item["sources"])
        req_refs.add(item["ref"])
    task_refs: set[str] = set()
    for item in value["tasks"]:
        if not isinstance(item, dict) or set(item) != task_fields:
            return None
        if not all(isinstance(item[name], str) and item[name] for name in ("ref", "objective", "implementation_scope")):
            return None
        if item["ref"] in task_refs:
            return None
        for name in ("acceptance_criteria", "required_checks", "requirement_refs", "dependency_refs"):
            if not isinstance(item[name], list) or any(not isinstance(entry, str) or not entry for entry in item[name]):
                return None
            if len(item[name]) != len(set(item[name])):
                return None
        if not item["acceptance_criteria"] or not item["requirement_refs"] or not set(item["requirement_refs"]) <= req_refs:
            return None
        task_refs.add(item["ref"])
    if any(not set(item["dependency_refs"]) <= task_refs or item["ref"] in item["dependency_refs"] for item in value["tasks"]):
        return None
    policies: set[str] = set()
    for item in value["execution_policy"]:
        if not isinstance(item, dict) or set(item) != policy_fields or item["task_ref"] not in task_refs or item["task_ref"] in policies:
            return None
        if not isinstance(item["wave"], int) or isinstance(item["wave"], bool) or item["wave"] < 1 or item["worker_profile"] not in {"routine", "complex"} or not isinstance(item["parallel_safe"], bool):
            return None
        if not isinstance(item["write_surfaces"], list) or not item["write_surfaces"] or any(not isinstance(entry, str) or not entry for entry in item["write_surfaces"]):
            return None
        policies.add(item["task_ref"])
    if policies != task_refs or (not value["ambiguities"] and (not req_refs or not task_refs)):
        return None
    implemented = {ref for item in value["tasks"] for ref in item["requirement_refs"]}
    for requirement in value["requirements"]:
        if requirement["implementation_required"] != (requirement["ref"] in implemented):
            return None
    # Validate the dependency graph and wave ordering deterministically.
    tasks = {item["ref"]: item for item in value["tasks"]}
    waves = {item["task_ref"]: item["wave"] for item in value["execution_policy"]}
    remaining = {ref: set(item["dependency_refs"]) for ref, item in tasks.items()}
    ready = sorted(ref for ref, deps in remaining.items() if not deps)
    visited: set[str] = set()
    while ready:
        ref = ready.pop(0); visited.add(ref)
        for candidate in sorted(remaining):
            if ref in remaining[candidate]:
                remaining[candidate].remove(ref)
                if not remaining[candidate] and candidate not in visited and candidate not in ready:
                    ready.append(candidate); ready.sort()
    if visited != task_refs or any(waves[dep] >= waves[ref] for ref, item in tasks.items() for dep in item["dependency_refs"]):
        return None
    policy_rows = value["execution_policy"]
    for index, left in enumerate(policy_rows):
        for right in policy_rows[index + 1:]:
            if left["wave"] != right["wave"] or not left["parallel_safe"] or not right["parallel_safe"]:
                continue
            for a in left["write_surfaces"]:
                for b in right["write_surfaces"]:
                    clean_a, clean_b = a.rstrip("/*"), b.rstrip("/*")
                    if clean_a == clean_b or clean_a.startswith(clean_b + "/") or clean_b.startswith(clean_a + "/"):
                        return None
    return value


def normalize_proposal_sources(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Store one source per locator while preserving every distinct excerpt."""
    normalized: list[dict[str, Any]] = []
    by_locator: dict[str, int] = {}
    excerpts: list[list[str]] = []
    for source in sources:
        locator = source["locator"]
        index = by_locator.get(locator)
        if index is None:
            by_locator[locator] = len(normalized)
            normalized.append({"locator": locator, "excerpt": None})
            excerpts.append([])
            index = len(normalized) - 1
        excerpt = source["excerpt"]
        if excerpt is not None and excerpt not in excerpts[index]:
            excerpts[index].append(excerpt)
    for index, values in enumerate(excerpts):
        normalized[index]["excerpt"] = "\n\n".join(values) if values else None
    return normalized


def proposal_sources_are_normalized(value: dict[str, Any]) -> bool:
    """Recognize the canonical source shape without changing approved JSON."""
    for requirement in value.get("requirements", []):
        sources = requirement.get("sources", [])
        if normalize_proposal_sources(sources) != sources:
            return False
    return True


class InitialPlanner:
    def __init__(self, *, service, project, journal: Journal, runtime, run_id: str, preparation_id: str,
                 spec_path: str, specification_bytes: bytes, specification_hash: str, config: PreparationConfig):
        self.service, self.project, self.journal, self.runtime = service, project, journal, runtime
        self.run_id, self.preparation_id, self.spec_path, self.specification_bytes, self.specification_hash, self.config = (
            run_id, preparation_id, spec_path, specification_bytes, specification_hash, config
        )

    async def run(self) -> tuple[dict[str, Any], str]:
        identity = self.runtime.session_identity(
            role=SessionRole.TASK_CREATOR, profile="taskledger_task_creator", subject_id=self.preparation_id,
            cwd=self.project["repository_root"], writable=False,
        )
        runtime_session = await self.runtime.start_session(
            role=SessionRole.TASK_CREATOR, profile="taskledger_task_creator", subject_id=self.preparation_id,
            cwd=self.project["repository_root"], writable=False,
        )
        session = self.journal.create_session(
            run_id=self.run_id, project_id=self.project["id"], role=SessionRole.TASK_CREATOR.value,
            profile="taskledger_task_creator", subject_id=self.preparation_id,
            external_thread_id=runtime_session.thread_id, config_hash=sha256(canonical(identity.as_dict())),
            runtime_identity=identity.as_dict(),
        )
        schema = proposal_schema()
        try:
            specification = self.specification_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise LedgerError("SPECIFICATION_STATE", "Preparation requires a UTF-8 specification revision.") from exc
        prompt = (
            "Inspect the repository and the immutable specification supplied below. Its path is " + self.spec_path +
            " and its SHA-256 is " + self.specification_hash + ". This exact content is authoritative; do not reopen "
            "the path to obtain specification content.\n\n--- immutable specification ---\n" + specification +
            "\n--- end immutable specification ---\n\nProduce the complete initial "
            "Taskledger proposal. Use temporary refs, deterministic checks, explicit dependencies, routine/complex routing, "
            "waves, parallel safety, and prospective write surfaces. Do not modify files. If product intent is materially "
            "ambiguous, list ambiguities and do not invent a plan. Return only the structured result."
        )
        for attempt in range(self.config.max_initial_planner_turns):
            if attempt and (
                self.journal.missing_usage_count(run_id=self.run_id)
                or self.journal.usage(run_id=self.run_id).total_tokens >= self.config.execution_limits["supervisor"]["max_total_tokens"]
            ):
                raise LedgerError("INVALID_REQUEST", "Initial planning cannot safely admit another model turn.")
            turn_prompt = prompt if attempt == 0 else "Return a corrected proposal satisfying the supplied schema and graph invariants."
            packet = PromptPacket(turn_prompt, DispatchReason.INITIAL_PLANNING, controller_payload_bytes=len(turn_prompt.encode()), output_schema_bytes=len(canonical(schema).encode()))
            local_id = self.journal.begin_turn(session.id, "initial-planning", packet=packet)
            handle = None
            try:
                handle = await self.runtime.start_turn(thread_id=session.thread_id, prompt=turn_prompt, output_schema=schema)
                self.journal.acknowledge_turn(local_id, handle)
                result = await asyncio.wait_for(self.runtime.wait_turn(handle), timeout=self.config.execution_limits["supervisor"]["turn_timeout_seconds"])
                self.journal.complete_turn(local_id, result)
            except Exception as exc:
                inspection = await self.runtime.inspect_turn(handle) if handle else None
                if inspection and inspection.state == "FAILED":
                    self.journal.fail_turn(local_id, inspection.error or str(exc), uncertain=False, result=inspection.result)
                    self.journal.consume_turn(local_id)
                    continue
                self.journal.fail_turn(local_id, str(exc), uncertain=True, result=inspection.result if inspection else None)
                raise
            terminal = self.journal.terminal_unconsumed_turn(session.id)
            proposal = validate_proposal(terminal.result.structured_output if terminal and terminal.result else None)
            if proposal is not None:
                self.journal.consume_turn(local_id)
                self.journal.close_session(session.id)
                return proposal, local_id
            self.journal.consume_turn(local_id)
        raise LedgerError("INVALID_REQUEST", "Task Creator did not produce a valid initial proposal within its turn budget.")


def store_planning_preparation(service, project, *, run_id: str, starting_oid: str, spec_id: str, spec_hash: str, profile_hashes: dict[str, Any], config: PreparationConfig) -> str:
    preparation_id, stamp = new_id(), now()
    config_json = canonical(config.as_dict())
    with transaction(service.con):
        service.con.execute(
            "INSERT INTO project_preparations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (preparation_id, project["id"], run_id, None, starting_oid, project["canonical_branch"], spec_id, spec_hash,
             canonical(profile_hashes), config_json, sha256(config_json), None, None, "PLANNING", None, None, stamp, stamp, None),
        )
    return preparation_id


def finish_preparation(service, preparation_id: str, proposal: dict[str, Any], turn_id: str) -> dict[str, Any]:
    digest = "sha256:" + sha256(canonical(proposal))
    state = "FAILED" if proposal["ambiguities"] else "AWAITING_APPROVAL"
    reason = "UNRESOLVED_AMBIGUITIES" if proposal["ambiguities"] else None
    with transaction(service.con):
        service.con.execute(
            "UPDATE project_preparations SET task_creator_turn_id=?,proposal_json=?,proposal_hash=?,state=?,failure_reason=?,updated_at=? WHERE id=? AND state='PLANNING'",
            (turn_id, canonical(proposal), digest, state, reason, now(), preparation_id),
        )
    return {"preparation_id": preparation_id, "proposal_hash": digest, "status": state, "proposal": proposal}


def preparation_row(service, preparation_id: str) -> dict[str, Any]:
    row = service.con.execute("SELECT * FROM project_preparations WHERE id=?", (preparation_id,)).fetchone()
    if not row:
        raise LedgerError("INVALID_REQUEST", "Preparation was not found.")
    value = dict(row)
    for source, target in (("profile_hashes_json", "profile_hashes"), ("run_configuration_json", "run_configuration"), ("proposal_json", "proposal")):
        raw = value.pop(source)
        value[target] = json.loads(raw) if raw else None
    return value


def materialized_plan(preparation: dict[str, Any]) -> dict[str, Any]:
    proposal = preparation["proposal"]
    spec_id = preparation["specification_id"]
    return {
        "requirements": [
            {**item, "sources": [{**source, "specification_id": spec_id} for source in item["sources"]]}
            for item in proposal["requirements"]
        ],
        "tasks": proposal["tasks"],
    }
