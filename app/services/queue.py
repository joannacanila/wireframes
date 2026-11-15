"""Asynchronous analysis queue (Figure 3.6: "checks whether more images remain").

One worker coroutine drains an ``asyncio.Queue`` and runs each CPU-bound analysis in a
worker thread, so the event loop keeps serving requests. A single consumer keeps model
inference serial (Ultralytics predictors are not thread-safe) and memory bounded.
On startup, analyses left in ``processing`` by a crash or restart are re-enqueued.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from sqlalchemy import select

from app.core.database import Database
from app.models.orm import Analysis

log = logging.getLogger(__name__)


class AnalysisQueue:
    def __init__(self, db: Database, job: Callable[[str], str]) -> None:
        self._db = db
        self._job = job
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self.processed = 0

    @property
    def depth(self) -> int:
        return self._queue.qsize()

    def submit(self, analysis_id: str) -> None:
        """Thread-safe: asyncio.Queue is bound to the worker's loop, so items from any other
        loop or thread are handed over with call_soon_threadsafe (otherwise the worker is never
        woken)."""
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if self._loop is not None and running is not self._loop:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, analysis_id)
        else:
            self._queue.put_nowait(analysis_id)

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        with self._db.session() as s:
            pending = s.scalars(
                select(Analysis.id).where(Analysis.status == "processing").order_by(Analysis.created_at)
            ).all()
        for aid in pending:
            self.submit(aid)
        if pending:
            log.info("re-enqueued %d unfinished analyses", len(pending))
        self._task = asyncio.create_task(self._run(), name="analysis-worker")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def drain(self) -> None:
        """Wait until every submitted analysis has finished (used by tests and CLI)."""
        await self._queue.join()

    async def _run(self) -> None:
        while True:
            aid = await self._queue.get()
            try:
                status = await asyncio.to_thread(self._job, aid)
                log.info("analysis %s -> %s", aid, status)
            except Exception:  # the job records its own errors; this guards the loop
                log.exception("worker crashed on %s", aid)
            finally:
                self.processed += 1
                self._queue.task_done()
