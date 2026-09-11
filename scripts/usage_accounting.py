"""Shared modern Codex response-usage accounting.

Only top-level ``token_usage_record`` events are measured.  Their per-response
``usage`` object is additive; cumulative turn/thread counters are reconciliation
signals and must never be added to it.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


TOKEN_FIELDS = (
    "input_tokens", "cached_input_tokens", "cache_write_input_tokens",
    "output_tokens", "reasoning_output_tokens", "total_tokens",
)


def timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class Session:
    path: Path
    metadata: dict
    thread_id: str
    run_id: str
    parent_thread_id: str | None


def session_metadata(path: Path) -> dict | None:
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if item.get("type") == "session_meta":
                    return item.get("payload", {})
    except OSError:
        return None
    return None


def parent_thread(metadata: dict) -> str | None:
    source = metadata.get("source")
    if isinstance(source, dict):
        subagent=source.get("subagent", {})
        spawn=subagent.get("thread_spawn", {}) if isinstance(subagent,dict) else {}
        if isinstance(spawn, dict) and spawn.get("parent_thread_id"):
            return str(spawn["parent_thread_id"])
    value = metadata.get("forked_from_id")
    return str(value) if value else None


def related_path(cwd: str | None, repo: Path) -> bool:
    if not cwd:
        return False
    try:
        working = Path(cwd).resolve()
        repository = repo.resolve()
        return working == repository or working in repository.parents or repository in working.parents
    except OSError:
        return False


def discover_sessions(root: Path, repo: Path, root_session_id: str | None = None) -> tuple[list[Session], dict]:
    sessions=[];files_scanned=0
    for path in root.rglob("*.jsonl"):
        files_scanned += 1
        metadata=session_metadata(path)
        if not metadata:
            continue
        thread_id=str(metadata.get("id") or metadata.get("thread_id") or metadata.get("session_id") or path.stem)
        run_id=str(metadata.get("session_id") or thread_id)
        sessions.append(Session(path,metadata,thread_id,run_id,parent_thread(metadata)))
    if root_session_id:
        selected_ids={root_session_id}
        selected_runs={session.run_id for session in sessions if session.thread_id==root_session_id or session.run_id==root_session_id}
    else:
        seeds=[session for session in sessions if related_path(session.metadata.get("cwd"),repo)]
        selected_ids={session.thread_id for session in seeds}
        selected_runs={session.run_id for session in seeds}
    changed=True
    while changed:
        changed=False
        for session in sessions:
            if session.thread_id in selected_ids:
                continue
            if session.run_id in selected_runs or session.parent_thread_id in selected_ids:
                selected_ids.add(session.thread_id);selected_runs.add(session.run_id);changed=True
    selected=[session for session in sessions if session.thread_id in selected_ids or session.run_id in selected_runs]
    selected.sort(key=lambda item:(str(item.metadata.get("timestamp","")),str(item.path)))
    return selected,{"files_scanned":files_scanned,"files_selected":len(selected),"root_session_id":root_session_id,
                     "selection":"explicit root plus descendants" if root_session_id else "repository-related roots plus descendants",
                     "parent_repository_cwds_included":True}


def _settings(payload: dict, item_type: str, model: str, effort: str) -> tuple[str,str]:
    if item_type == "turn_context":
        collaboration=payload.get("collaboration_mode",{}).get("settings",{})
        return str(payload.get("model",model)),str(payload.get("effort",collaboration.get("reasoning_effort",effort)))
    if item_type == "world_state":
        state=payload.get("state",{})
        return str(state.get("model",model)),str(state.get("model_reasoning_effort",state.get("reasoning_effort",effort)))
    if payload.get("type") == "thread_settings_applied":
        settings=payload.get("thread_settings",{})
        return str(settings.get("model",model)),str(settings.get("model_reasoning_effort",settings.get("reasoning_effort",effort)))
    return model,effort


def _session_attribution(session: Session) -> tuple[str, str]:
    """Return stable, non-secret source and agent-role labels for a session."""
    thread_source = str(session.metadata.get("thread_source") or "unknown")
    agent_role = "root"
    metadata_source = session.metadata.get("source")
    if isinstance(metadata_source, dict):
        subagent = metadata_source.get("subagent")
        spawn = subagent.get("thread_spawn") if isinstance(subagent, dict) else None
        if isinstance(spawn, dict) and spawn.get("agent_role"):
            agent_role = str(spawn["agent_role"])
        elif thread_source == "subagent":
            agent_role = "subagent_unspecified"
    return thread_source, agent_role


def collect_usage(sessions: list[Session], since: datetime | None = None, until: datetime | None = None,
                  windows: list[tuple[datetime,datetime,str]] | None = None) -> dict:
    cohorts=defaultdict(Counter);window_cohorts=defaultdict(lambda:defaultdict(Counter))
    source_totals=defaultdict(Counter);role_totals=defaultdict(Counter)
    response_ids=set();duplicates=0;invalid=0;records=0;compactions=0
    final_counters={};thread_sums=defaultdict(Counter)
    for session in sessions:
        model=effort="unknown"
        thread_source,agent_role=_session_attribution(session)
        try:
            handle=session.path.open(encoding="utf-8")
        except OSError:
            invalid += 1;continue
        with handle:
            for line in handle:
                try:item=json.loads(line)
                except json.JSONDecodeError:
                    invalid += 1;continue
                payload=item.get("payload",{});model,effort=_settings(payload,item.get("type",""),model,effort)
                raw_time=item.get("timestamp")
                if not raw_time:continue
                event_time=timestamp(raw_time)
                if since and event_time<since:continue
                if until and event_time>=until:continue
                if payload.get("type")=="context_compacted":compactions += 1
                if item.get("type")!="token_usage_record":continue
                response_id=payload.get("response_id")
                usage=payload.get("usage")
                if not response_id or not isinstance(usage,dict):invalid += 1;continue
                if response_id in response_ids:duplicates += 1;continue
                response_ids.add(response_id);records += 1
                cohort=f"{model}|{effort}"
                for field in TOKEN_FIELDS:
                    amount=int(usage.get(field,0) or 0)
                    cohorts[cohort][field]+=amount;thread_sums[session.thread_id][field]+=amount
                    source_totals[thread_source][field]+=amount;role_totals[agent_role][field]+=amount
                counter=payload.get("thread_token_usage")
                if isinstance(counter,dict):final_counters[session.thread_id]={field:int(counter.get(field,0) or 0) for field in TOKEN_FIELDS}
                for start,end,key in windows or []:
                    if start<=event_time<=end:
                        for field in TOKEN_FIELDS:window_cohorts[key][cohort][field]+=int(usage.get(field,0) or 0)
                        window_cohorts[key][cohort]["response_records"]+=1
    totals=Counter()
    for values in cohorts.values():totals.update(values)
    thread_reconciliation=[]
    for thread_id,counter in sorted(final_counters.items()):
        measured={field:thread_sums[thread_id][field] for field in TOKEN_FIELDS}
        differences={field:measured[field]-counter[field] for field in TOKEN_FIELDS if measured[field]!=counter[field]}
        thread_reconciliation.append({"thread_id":thread_id,"measured_response_sum":measured,"final_counter":counter,
                                      "status":"CONSISTENT" if not differences else "INCONSISTENT_OR_PARTIAL_WINDOW",
                                      "differences":differences,"counter_added_to_totals":False})
    telemetry_status="MEASURED" if records else "MISSING"
    return {"telemetry_status":telemetry_status,"measurement":"modern token_usage_record usage, response-ID deduplicated",
            "billing_interpretation":"not inferred","reasoning_tokens":"reported as an output subset and never added to total_tokens",
            "response_records":records,"duplicate_response_records_excluded":duplicates,"invalid_records":invalid,
            "compactions":compactions,"cohorts":{key:dict(value) for key,value in sorted(cohorts.items())},
            "usage_by_thread_source":{key:dict(value) for key,value in sorted(source_totals.items())},
            "usage_by_agent_role":{key:dict(value) for key,value in sorted(role_totals.items())},
            "totals":dict(totals),"thread_counter_reconciliation":thread_reconciliation,
            "window_cohorts":{task:{cohort:dict(values) for cohort,values in sorted(groups.items())} for task,groups in window_cohorts.items()}}
