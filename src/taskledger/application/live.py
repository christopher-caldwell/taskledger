from __future__ import annotations

import asyncio
import threading
from typing import Any


class LatestSnapshotFeed:
    def __init__(self):
        self._lock = threading.Lock(); self.latest = None; self.reader = None

    def publish(self, snapshot: Any) -> bool:
        with self._lock:
            if self.latest and snapshot.version.engine_epoch == self.latest.version.engine_epoch and snapshot.version.revision <= self.latest.version.revision: return False
            self.latest = snapshot; reader = self.reader
            if reader: reader._offer_locked(snapshot)
        return True

    def attach(self, loop: asyncio.AbstractEventLoop):
        with self._lock:
            if self.reader is not None: raise RuntimeError("DISPLAY_ALREADY_ATTACHED")
            self.reader = FeedReader(self, loop)
            if self.latest is not None: self.reader._offer_locked(self.latest)
            return self.reader


class FeedReader:
    def __init__(self, feed, loop):
        self.feed, self.loop = feed, loop; self.pending = None; self.wake_queued = False; self.closed = False; self.available = asyncio.Event()

    def _offer_locked(self, value):
        self.pending = value
        if not self.wake_queued:
            self.wake_queued = True
            try: self.loop.call_soon_threadsafe(self._wake)
            except RuntimeError: self.closed = True; self.feed.reader = None; self.wake_queued = False

    def _wake(self):
        with self.feed._lock: self.wake_queued = False
        self.available.set()

    async def receive(self):
        while True:
            with self.feed._lock:
                if self.closed: raise StopAsyncIteration
                if self.pending is not None:
                    value, self.pending = self.pending, None
                    return value
                self.available.clear()
            await self.available.wait()

    def close(self):
        with self.feed._lock:
            self.closed = True
            if self.feed.reader is self: self.feed.reader = None
        self.available.set()
