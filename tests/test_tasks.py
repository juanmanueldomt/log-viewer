from __future__ import annotations

import queue
import threading
from collections.abc import Generator
from typing import Any

import pytest

from jmlogviewer.core.tasks import Event, Job, ManualExecutor, Priority, Scheduler

Events = queue.SimpleQueue[tuple[Job, Event, Any]]


def counter(name: str, steps: int, log: list[str]) -> Generator[int | None, None, str]:
    for i in range(steps):
        log.append(name)
        yield i if i % 2 else None
    return f"{name} done"


def wait_for(events: Events, count: int) -> list[tuple[Job, Event, Any]]:
    """Collect events until *count* jobs finished."""
    seen: list[tuple[Job, Event, Any]] = []
    finished = 0
    while finished < count:
        item = events.get(timeout=5)
        seen.append(item)
        if item[1] is not Event.PROGRESS:
            finished += 1
    return seen


@pytest.fixture
def scheduler() -> Generator[tuple[Scheduler, Events]]:
    events: Events = queue.SimpleQueue()
    instance = Scheduler(lambda job, event, payload: events.put((job, event, payload)))
    yield instance, events
    instance.shutdown()


def test_scheduler_runs_jobs_and_reports_results(scheduler: tuple[Scheduler, Events]) -> None:
    instance, events = scheduler
    log: list[str] = []
    job = Job(lambda: counter("a", 5, log), Priority.SEARCH, None, None, None)
    instance.submit(job)
    seen = wait_for(events, 1)
    assert [(e, p) for _, e, p in seen] == [
        (Event.PROGRESS, 1),
        (Event.PROGRESS, 3),
        (Event.DONE, "a done"),
    ]
    assert log == ["a"] * 5


def test_urgent_jobs_preempt_long_ones(scheduler: tuple[Scheduler, Events]) -> None:
    instance, events = scheduler
    log: list[str] = []
    started = threading.Event()
    release = threading.Event()

    def slow() -> Generator[None, None, str]:
        started.set()
        release.wait(5)
        for _ in range(50):
            log.append("slow")
            yield None
        return "slow done"

    instance.submit(Job(slow, Priority.ANALYSIS, None, None, None))
    started.wait(5)
    instance.submit(Job(lambda: counter("fast", 3, log), Priority.SEARCH, None, None, None))
    release.set()
    wait_for(events, 2)
    first_fast = log.index("fast")
    assert first_fast <= 1  # the running step finished, then the urgent job ran
    assert log[first_fast : first_fast + 3] == ["fast"] * 3
    assert log.count("slow") == 50


def test_errors_and_cancellation(scheduler: tuple[Scheduler, Events]) -> None:
    instance, events = scheduler

    def broken() -> Generator[None, None, None]:
        yield None
        raise ValueError("boom")

    cancelled = Job(lambda: counter("c", 3, []), Priority.SEARCH, None, None, None)
    cancelled.cancel()
    instance.submit(Job(broken, Priority.SEARCH, None, None, None))
    instance.submit(cancelled)
    seen = wait_for(events, 2)
    kinds = [event for _, event, _ in seen]
    assert Event.ERROR in kinds
    assert Event.CANCELLED in kinds
    error = next(payload for _, event, payload in seen if event is Event.ERROR)
    assert isinstance(error, ValueError)


def test_dispatch_skips_cancelled_jobs() -> None:
    results: list[object] = []
    job = Job(lambda: counter("x", 1, []), Priority.SEARCH, results.append, results.append, None)
    job.dispatch(Event.PROGRESS, 1)
    job.cancel()
    job.dispatch(Event.DONE, 2)
    assert results == [1]


def test_manual_executor_runs_by_priority() -> None:
    executor = ManualExecutor()
    order: list[str] = []
    for name, priority in (("analysis", Priority.ANALYSIS), ("index", Priority.INDEX)):
        executor.submit(
            lambda name=name: counter(name, 1, order),  # type: ignore[misc]
            priority=priority,
            on_done=order.append,
        )
    assert executor.pending == 2
    assert executor.run_pending() == 2
    assert order == ["index", "index done", "analysis", "analysis done"]


def test_manual_executor_reports_errors() -> None:
    executor = ManualExecutor()
    errors: list[BaseException] = []

    def broken() -> Generator[None, None, None]:
        raise RuntimeError("nope")
        yield None

    executor.submit(broken, priority=Priority.SEARCH, on_error=errors.append)
    executor.run_pending()
    assert [str(e) for e in errors] == ["nope"]
