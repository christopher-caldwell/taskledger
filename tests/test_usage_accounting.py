from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT=Path(__file__).parents[1]
sys.path.insert(0,str(ROOT/"scripts"))

from usage_accounting import collect_usage,discover_sessions


class UsageAccountingTests(unittest.TestCase):
    def write_rollout(self, directory: Path, name: str, events: list[dict]):
        path=directory/f"{name}.jsonl"
        path.write_text("".join(json.dumps(event)+"\n" for event in events),encoding="utf-8")
        return path

    def test_modern_usage_is_deduplicated_and_discovers_parent_root_and_children(self):
        with tempfile.TemporaryDirectory() as raw:
            directory=Path(raw);repo=directory/"parent"/"nested";repo.mkdir(parents=True)
            root_meta={"id":"root-thread","session_id":"run-1","timestamp":"2026-01-01T00:00:00Z","cwd":str(repo.parent),"thread_source":"user"}
            usage={"input_tokens":100,"cached_input_tokens":40,"cache_write_input_tokens":0,"output_tokens":20,"reasoning_output_tokens":7,"total_tokens":120}
            root_record={"timestamp":"2026-01-01T00:01:00Z","type":"token_usage_record","payload":{"response_id":"response-1","usage":usage,"thread_token_usage":usage}}
            legacy={"timestamp":"2026-01-01T00:01:01Z","type":"event_msg","payload":{"type":"token_count","info":{"last_token_usage":{"total_tokens":999999}}}}
            self.write_rollout(directory,"root",[{"type":"session_meta","payload":root_meta},{"type":"turn_context","timestamp":"2026-01-01T00:00:30Z","payload":{"model":"primary-model","effort":"medium"}},root_record,legacy])
            child_meta={"id":"child-thread","session_id":"run-1","timestamp":"2026-01-01T00:02:00Z","cwd":str(repo),"thread_source":"subagent","source":{"subagent":{"thread_spawn":{"parent_thread_id":"root-thread","agent_role":"taskledger_worker_routine"}}}}
            child_usage={"input_tokens":50,"cached_input_tokens":0,"cache_write_input_tokens":0,"output_tokens":10,"reasoning_output_tokens":4,"total_tokens":60}
            self.write_rollout(directory,"child",[{"type":"session_meta","payload":child_meta},{"type":"turn_context","timestamp":"2026-01-01T00:02:01Z","payload":{"model":"worker-model","effort":"high"}},root_record,{"timestamp":"2026-01-01T00:03:00Z","type":"token_usage_record","payload":{"response_id":"response-2","usage":child_usage,"thread_token_usage":child_usage}}])
            sessions,discovery=discover_sessions(directory,repo,"run-1")
            self.assertEqual(discovery["files_selected"],2)
            measured=collect_usage(sessions)
            self.assertEqual(measured["response_records"],2)
            self.assertEqual(measured["duplicate_response_records_excluded"],1)
            self.assertEqual(measured["totals"]["total_tokens"],180)
            self.assertEqual(measured["totals"]["output_tokens"],30)
            self.assertEqual(measured["totals"]["reasoning_output_tokens"],11)
            self.assertNotEqual(measured["totals"]["output_tokens"]+measured["totals"]["reasoning_output_tokens"],measured["totals"]["total_tokens"])
            self.assertEqual(set(measured["cohorts"]),{"primary-model|medium","worker-model|high"})
            self.assertEqual(measured["usage_by_thread_source"]["user"]["total_tokens"],120)
            self.assertEqual(measured["usage_by_thread_source"]["subagent"]["total_tokens"],60)
            self.assertEqual(measured["usage_by_agent_role"]["root"]["total_tokens"],120)
            self.assertEqual(measured["usage_by_agent_role"]["taskledger_worker_routine"]["total_tokens"],60)
            self.assertEqual(measured["telemetry_status"],"MEASURED")

    def test_missing_modern_telemetry_is_explicit(self):
        with tempfile.TemporaryDirectory() as raw:
            directory=Path(raw);repo=directory/"repo";repo.mkdir()
            meta={"id":"root","session_id":"root","timestamp":"2026-01-01T00:00:00Z","cwd":str(repo)}
            self.write_rollout(directory,"legacy-only",[{"type":"session_meta","payload":meta},{"timestamp":"2026-01-01T00:01:00Z","type":"event_msg","payload":{"type":"token_count","info":{"last_token_usage":{"total_tokens":123}}}}])
            sessions,_=discover_sessions(directory,repo)
            measured=collect_usage(sessions)
            self.assertEqual(measured["telemetry_status"],"MISSING")
            self.assertEqual(measured["response_records"],0)
            self.assertEqual(measured["totals"],{})


if __name__=="__main__":unittest.main()
