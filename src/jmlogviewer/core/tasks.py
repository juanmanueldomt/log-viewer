"""Background work.

Slow work (indexing, scanning) is written as generators that do a small step of
work per iteration and yield progress (or ``None`` when there is nothing to
report). A :class:`Scheduler` advances them on a single background thread, most
urgent first, so a search never waits behind a long analysis. A single thread
also matters for responsiveness: with CPython's GIL, several CPU-bound threads
would starve the UI thread.

The core only depends on the :class:`Executor` protocol. The UI adapts the
scheduler so callbacks run on the Tk thread; tests use :class:`ManualExecutor`.
"""

from __future__ import annotations

import heapq
import itertools
import logging
import threading
from collections.abc import Callable, Generator
from enum import IntEnum
from typing import Any, Protocol, TypeVar

P = TypeVar("P")
R = TypeVar("R")

log = logging.getLogger(__name__)

Work = Callable[[], Generator[P | None, None, R]]
"""A job: a generator function yielding progress (or ``None``) and returning a result."""


class Priority(IntEnum):
    """Lower runs first."""

    INDEX = 0
    SEARCH = 1
    ANALYSIS = 2


class Event(IntEnum):
    PROGRESS = 0
    DONE = 1
    ERROR = 2
    CANCELLED = 3


class Cancelled(Exception):  # noqa: N818 - reads naturally as "raise Cancelled"
    """Raised to abandon a job whose token was cancelled."""


class CancelToken:
    """Thread-safe cancellation flag."""

    __slots__ = ("_event",)

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self._event.is_set():
            raise Cancelled


class JobHandle(Protocol):
    def cancel(self) -> None:
        """Stop the job; none of its callbacks are called after this."""


class Executor(Protocol):
    def submit(
        self,
        work: Work[P, R],
        *,
        priority: Priority,
        on_progress: Callable[[P], None] | None = None,
        on_done: Callable[[R], None] | None = None,
        on_error: Callable[[BaseException], None] | None = None,
    ) -> JobHandle:
        """Run *work* in the background.

        Callbacks run on the owner's thread, in order: every progress report
        precedes ``on_done``/``on_error``, and none runs after cancellation.
        """
        ...


class Job:
    """A submitted piece of work and the callbacks for its results."""

    __slots__ = ("_steps", "on_done", "on_error", "on_progress", "priority", "token", "work")

    def __init__(
        self,
        work: Work[Any, Any],
        priority: Priority,
        on_progress: Callable[[Any], None] | None,
        on_done: Callable[[Any], None] | None,
        on_error: Callable[[BaseException], None] | None,
    ) -> None:
        self.work = work
        self.priority = priority
        self.on_progress = on_progress
        self.on_done = on_done
        self.on_error = on_error
        self.token = CancelToken()
        self._steps: Generator[Any, None, Any] | None = None

    def cancel(self) -> None:
        self.token.cancel()

    def step(self) -> tuple[Event, Any] | None:
        """Advance one step; return an event to report, if any."""
        try:
            if self._steps is None:
                self._steps = self.work()
            item = next(self._steps)
        except StopIteration as stop:
            return Event.DONE, stop.value
        except Cancelled:
            return Event.CANCELLED, None
        except Exception as exc:
            return Event.ERROR, exc
        return None if item is None else (Event.PROGRESS, item)

    def dispatch(self, event: Event, payload: Any) -> None:
        """Call the callback for *event* (on the owner's thread)."""
        if self.token.cancelled or event is Event.CANCELLED:
            return
        callback = {
            Event.PROGRESS: self.on_progress,
            Event.DONE: self.on_done,
            Event.ERROR: self.on_error,
        }[event]
        if callback is not None:
            callback(payload)
        elif event is Event.ERROR:
            log.error("Background job failed", exc_info=payload)


class Scheduler:
    """Advances jobs on one background thread, a step at a time, most urgent first.

    Events are handed to *report* on the worker thread; it must be thread-safe
    (for instance ``queue.put``) and route them to the owner's thread.
    """

    def __init__(self, report: Callable[[Job, Event, Any], None]) -> None:
        self._report = report
        self._ready: list[tuple[int, int, Job]] = []  # heap of (priority, order, job)
        self._order = itertools.count()
        self._condition = threading.Condition()
        self._closed = False
        self._thread = threading.Thread(target=self._run, name="jmlogviewer-worker", daemon=True)
        self._thread.start()

    def submit(self, job: Job) -> None:
        with self._condition:
            heapq.heappush(self._ready, (job.priority, next(self._order), job))
            self._condition.notify()

    def shutdown(self) -> None:
        with self._condition:
            self._closed = True
            for _priority, _order, job in self._ready:
                job.cancel()
            self._ready.clear()
            self._condition.notify()

    def _run(self) -> None:
        while True:
            with self._condition:
                while not self._ready and not self._closed:
                    self._condition.wait()
                if self._closed:
                    return
                priority, order, job = heapq.heappop(self._ready)
            self._advance(job, priority, order)

    def _advance(self, job: Job, priority: int, order: int) -> None:
        """Run *job* until it ends or a more urgent job is waiting."""
        while True:
            if job.token.cancelled:
                self._report(job, Event.CANCELLED, None)
                return
            event = job.step()
            if event is not None:
                self._report(job, *event)
                if event[0] is not Event.PROGRESS:
                    return
            with self._condition:
                if self._closed:
                    return
                if self._ready and self._ready[0][0] < priority:
                    heapq.heappush(self._ready, (priority, order, job))  # resume later
                    return


class ManualExecutor:
    """Runs jobs only when asked, synchronously; for tests and scripts."""

    def __init__(self) -> None:
        self._jobs: list[tuple[int, int, Job]] = []
        self._order = itertools.count()

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
        heapq.heappush(self._jobs, (priority, next(self._order), job))
        return job

    @property
    def pending(self) -> int:
        return len(self._jobs)

    def run_pending(self, limit: int = 10_000) -> int:
        """Run queued jobs (and the ones they queue) to completion; return how many ran."""
        ran = 0
        while self._jobs and ran < limit:
            _priority, _order, job = heapq.heappop(self._jobs)
            ran += 1
            while not job.token.cancelled:
                event = job.step()
                if event is None:
                    continue
                job.dispatch(*event)
                if event[0] is not Event.PROGRESS:
                    break
        return ran
