"""Runs background jobs on the worker thread and reports back on the Tk thread."""

from __future__ import annotations

import logging
import queue
import time
import tkinter as tk
from collections.abc import Callable
from typing import Any

from ..core.tasks import Event, Job, JobHandle, P, Priority, R, Scheduler, Work

log = logging.getLogger(__name__)


class TkExecutor:
    """Executor whose callbacks run on the Tk thread.

    Tk must only be used from the thread running the main loop, so the worker
    posts events to a queue that the Tk thread drains with ``after``.
    """

    def __init__(self, widget: tk.Misc, *, poll_ms: int = 20) -> None:
        self._widget = widget
        self._poll_ms = poll_ms
        self._events: queue.SimpleQueue[tuple[Job, Event, Any]] = queue.SimpleQueue()
        self._scheduler = Scheduler(
            lambda job, event, payload: self._events.put((job, event, payload))
        )
        self._unfinished = 0
        self._after_id: str | None = None
        self._closed = False

    def submit(
        self,
        work: Work[P, R],
        *,
        priority: Priority,
        on_progress: Callable[[P], None] | None = None,
        on_done: Callable[[R], None] | None = None,
        on_error: Callable[[BaseException], None] | None = None,
    ) -> JobHandle:
        job = Job(work, priority, on_progress, on_done, on_error)
        if self._closed:
            job.cancel()
            return job
        self._unfinished += 1
        self._scheduler.submit(job)
        self._schedule()
        return job

    def shutdown(self) -> None:
        self._closed = True
        if self._after_id is not None:
            self._widget.after_cancel(self._after_id)
            self._after_id = None
        self._scheduler.shutdown()

    def _schedule(self) -> None:
        if self._after_id is None and not self._closed:
            self._after_id = self._widget.after(self._poll_ms, self._drain)

    def _drain(self) -> None:
        self._after_id = None
        deadline = time.monotonic() + 0.03  # leave the Tk thread time to repaint
        while time.monotonic() < deadline:
            try:
                job, event, payload = self._events.get_nowait()
            except queue.Empty:
                break
            if event is not Event.PROGRESS:
                self._unfinished -= 1
            try:
                job.dispatch(event, payload)
            except Exception:
                log.exception("Error while handling a background job result")
        if self._unfinished > 0 or not self._events.empty():
            self._schedule()
