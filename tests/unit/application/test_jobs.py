"""The job registry behind every long-running operation."""

from __future__ import annotations

import threading

import pytest

import sendspin_bridge.application.jobs as jobs_module
from sendspin_bridge.application.errors import UseCaseError
from sendspin_bridge.application.jobs import JobRegistry


def _wait(registry: JobRegistry, job_id: str, timeout: float = 2.0):
    import time

    deadline = time.monotonic() + timeout
    while (job := registry.get(job_id)).status == "running":
        assert time.monotonic() < deadline, "job never finished"
        time.sleep(0.005)
    return job


def test_a_job_reports_its_result():
    registry = JobRegistry()
    job = registry.start("demo", lambda _ctx: {"answer": 42}, subject="x")
    assert job.status == "running"
    done = _wait(registry, job.id)
    assert done.status == "succeeded"
    assert done.result == {"answer": 42}
    assert done.subject == "x"


def test_a_refusal_keeps_its_code_and_status():
    registry = JobRegistry()

    def _refuse(_ctx):
        raise UseCaseError(422, "pairing_failed", "Speaker said no")

    done = _wait(registry, registry.start("demo", _refuse).id)
    assert done.status == "failed"
    assert (done.error.code, done.error.status, done.error.detail) == ("pairing_failed", 422, "Speaker said no")


def test_an_unexpected_error_hides_its_internals():
    registry = JobRegistry()

    def _crash(_ctx):
        raise RuntimeError("/etc/secret path in a message")

    done = _wait(registry, registry.start("demo", _crash).id)
    assert done.error.code == "internal_error"
    assert "secret" not in (done.error.detail or "")


def test_progress_merges_and_is_published():
    registry = JobRegistry()
    seen: list[dict] = []
    registry.add_listener(lambda job: seen.append(dict(job.progress)))
    gate = threading.Event()

    def _work(ctx):
        ctx.progress(step="one")
        ctx.progress(percent=50)
        gate.wait(2)
        return None

    job = registry.start("demo", _work, progress={"started": True})
    import time

    while registry.get(job.id).progress.get("percent") != 50:
        time.sleep(0.005)
    assert registry.get(job.id).progress == {"started": True, "step": "one", "percent": 50}
    gate.set()
    _wait(registry, job.id)
    assert {"started": True} in seen


def test_exclusive_kinds_refuse_a_second_start():
    registry = JobRegistry()
    gate = threading.Event()
    first = registry.start("scan", lambda _ctx: gate.wait(2), exclusive=True)
    try:
        with pytest.raises(UseCaseError) as exc:
            registry.start("scan", lambda _ctx: None, exclusive=True)
        assert exc.value.status == 409
    finally:
        gate.set()
        _wait(registry, first.id)
    registry.start("scan", lambda _ctx: None, exclusive=True)


def test_cancel_asks_the_work_to_stop():
    registry = JobRegistry()

    def _work(ctx):
        ctx.cancel_requested.wait(2)
        return "stopped"

    job = registry.start("demo", _work)
    registry.cancel(job.id)
    assert _wait(registry, job.id).status == "cancelled"


def test_unknown_jobs_are_404():
    with pytest.raises(UseCaseError) as exc:
        JobRegistry().get("nope")
    assert exc.value.status == 404


def test_finished_jobs_expire(monkeypatch):
    registry = JobRegistry()
    job = registry.start("demo", lambda _ctx: None)
    _wait(registry, job.id)
    monkeypatch.setattr(jobs_module, "_TTL_S", -1.0)
    with pytest.raises(UseCaseError):
        registry.get(job.id)


def test_a_job_whose_thread_cannot_start_does_not_stay_running(monkeypatch):
    """Otherwise an exclusive kind refuses every later start until a restart."""
    registry = JobRegistry()

    class _DeadThread:
        def __init__(self, *_a, **_kw):
            pass

        def start(self):
            raise RuntimeError("can't start new thread")

    monkeypatch.setattr(jobs_module.threading, "Thread", _DeadThread)
    with pytest.raises(RuntimeError):
        registry.start("scan", lambda _ctx: None, exclusive=True)
    assert registry.running("scan") == []
