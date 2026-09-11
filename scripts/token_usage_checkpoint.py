#!/usr/bin/env python3
"""Create a privacy-safe checkpoint from modern Codex usage telemetry."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import Counter
from pathlib import Path

from usage_accounting import collect_usage, discover_sessions, timestamp


COMMAND = re.compile(
    r"(?:^|[\s\"'])taskledger\s+"
    r"(project|spec|requirement|task|plan|assignment|worker|submission|checkpoint|artifact|evidence|blocker|question|follow-up|operation)\s+"
    r"([a-z][a-z-]*)"
)


def arguments() -> argparse.Namespace:
    parser=argparse.ArgumentParser()
    parser.add_argument("--repo",required=True,type=Path)
    parser.add_argument("--sessions",type=Path,default=Path.home()/".codex"/"sessions")
    parser.add_argument("--since",required=True)
    parser.add_argument("--until",required=True)
    parser.add_argument("--label",required=True)
    parser.add_argument("--taskledger-version",required=True)
    parser.add_argument("--session-id",help="Root run/session id; descendants are discovered explicitly")
    parser.add_argument("--ledger-db",type=Path)
    parser.add_argument("--output",required=True,type=Path)
    return parser.parse_args()


def ledger_events(database: Path | None, repo: Path, since: str, until: str) -> dict:
    if not database or not database.is_file():return {"available":False,"reason":"ledger database not supplied or unavailable"}
    connection=sqlite3.connect(database.as_uri()+"?immutable=1",uri=True)
    try:
        project=connection.execute("SELECT id FROM projects WHERE repository_root=?",(str(repo),)).fetchone()
        if not project:return {"available":False,"reason":"repository not found in ledger"}
        counts=dict(connection.execute("SELECT event_type,COUNT(*) FROM audit_events WHERE project_id=? AND created_at>=? AND created_at<? GROUP BY event_type ORDER BY event_type",(project[0],since,until)))
        return {"available":True,"project_id":project[0],"event_counts":counts}
    finally:connection.close()


def command_counts(sessions, since, until):
    counts=Counter();cli_versions=Counter();sources=Counter();roles=Counter()
    for session in sessions:
        cli_versions[str(session.metadata.get("cli_version","unknown"))]+=1
        sources[str(session.metadata.get("thread_source","unknown"))]+=1
        source=session.metadata.get("source",{});role="none"
        if isinstance(source,dict):role=str(source.get("subagent",{}).get("thread_spawn",{}).get("agent_role","none"))
        roles[role]+=1
        with session.path.open(encoding="utf-8") as handle:
            for line in handle:
                try:item=json.loads(line)
                except json.JSONDecodeError:continue
                raw_time=item.get("timestamp")
                if not raw_time or not since<=timestamp(raw_time)<until:continue
                payload=item.get("payload",{})
                if item.get("type")=="response_item" and payload.get("type")=="custom_tool_call":
                    for resource,action in COMMAND.findall(payload.get("input","") or ""):counts[f"{resource} {action}"]+=1
    return {"taskledger_commands":dict(counts.most_common()),"codex_cli_versions":dict(cli_versions),
            "thread_sources":dict(sources),"agent_roles":dict(roles)}


def main() -> None:
    args=arguments();repo=args.repo.resolve();since=timestamp(args.since);until=timestamp(args.until)
    sessions,discovery=discover_sessions(args.sessions,repo,args.session_id)
    usage=collect_usage(sessions,since,until)
    result={"schema_version":2,"label":args.label,"taskledger_version":args.taskledger_version,
            "repository":str(repo),"window":{"since":since.isoformat(),"until":until.isoformat()},
            "source":{"sessions":str(args.sessions),**discovery,**command_counts(sessions,since,until)},
            "usage":usage,"durable_progress":ledger_events(args.ledger_db,repo,since.isoformat(),until.isoformat()),
            "interpretation":{"tokens_are_measured_not_billing":True,"modern_telemetry_required":True,
                              "legacy_token_count_ignored":True,"missing_or_inconsistent_telemetry_is_explicit":True,
                              "compare_like_model_and_effort_cohorts":True}}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2,sort_keys=True)+"\n",encoding="utf-8")


if __name__=="__main__":main()
