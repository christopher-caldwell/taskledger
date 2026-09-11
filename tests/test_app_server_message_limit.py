"""Zero-model regression coverage using the real subprocess JSONL transport."""

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from taskledger.controller.app_server import AppServerRuntime
from taskledger.controller.model import RuntimeTurnHandle


class AppServerMessageLimitTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        executable = self.root / "fake-codex"
        executable.write_text(
            f"#!{sys.executable}\n"
            "import json, sys\n"
            "if '--version' in sys.argv:\n"
            "    print('zero-model-test'); sys.exit(0)\n"
            "if 'mcp' in sys.argv:\n"
            "    print('[]'); sys.exit(0)\n"
            "for line in sys.stdin:\n"
            "    request = json.loads(line)\n"
            "    if 'id' not in request: continue\n"
            "    result = {}\n"
            "    if request['method'] == 'thread/read':\n"
            "        result = {'thread': {'id': 'preserved', 'turns': [\n"
            "            {'id': 'turn', 'status': 'completed', 'items': [\n"
            "                {'type': 'agentMessage', 'text': 'x' * request['params']['size']}]}]}}\n"
            "    print(json.dumps({'id': request['id'], 'result': result}), flush=True)\n"
        )
        executable.chmod(0o700)
        self.runtime = AppServerRuntime(
            repository_root=str(self.root), codex_executable=str(executable)
        )

    async def asyncTearDown(self):
        await self.runtime.close()
        self.temp.cleanup()

    async def test_processes_codex_jsonl_message_larger_than_64_kib(self):
        await self.runtime._ensure_started()
        result = await asyncio.wait_for(
            self.runtime._request("thread/read", {"size": 128 * 1024}), 5
        )
        self.assertGreater(len(json.dumps(result).encode()), 64 * 1024)
        self.assertEqual(result["thread"]["turns"][0]["items"][0]["text"], "x" * (128 * 1024))
        # The reader must remain usable after processing the large message.
        self.assertEqual(await self.runtime._request("initialize", {}), {})

    async def test_rejects_message_over_explicit_bound(self):
        await self.runtime._ensure_started()
        with self.assertRaisesRegex(RuntimeError, "Taskledger app-server message exceeded the explicit"):
            await asyncio.wait_for(
                self.runtime._request("thread/read", {"size": self.runtime.MAX_MESSAGE_BYTES + 1}), 5
            )

    async def test_processes_multiple_large_messages(self):
        await self.runtime._ensure_started()
        for size in (96 * 1024, 192 * 1024, 80 * 1024):
            result = await asyncio.wait_for(self.runtime._request("thread/read", {"size": size}), 5)
            self.assertEqual(len(result["thread"]["turns"][0]["items"][0]["text"]), size)

    async def test_interrupted_provider_turn_is_a_semantic_failure(self):
        handle = RuntimeTurnHandle("thread", "turn")
        with patch.object(self.runtime, "_ensure_started", new=AsyncMock()), patch.object(
            self.runtime, "_request",
            new=AsyncMock(return_value={"thread": {"turns": [{"id": "turn", "status": "interrupted"}]}}),
        ):
            inspection = await self.runtime.inspect_turn(handle)
        self.assertEqual(inspection.state, "FAILED")
        self.assertEqual(inspection.error, "INTERRUPTED")

    async def test_interrupt_turn_uses_recorded_thread_and_turn_ids(self):
        handle = RuntimeTurnHandle("thread", "turn")
        request = AsyncMock(return_value={})
        with patch.object(self.runtime, "_request", new=request):
            await self.runtime.interrupt_turn(handle)
        request.assert_awaited_once_with("turn/interrupt", {"threadId": "thread", "turnId": "turn"})


if __name__ == "__main__":
    unittest.main()
