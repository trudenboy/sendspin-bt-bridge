"""Rate limiting of forwarded daemon log lines (issue #345).

When a daemon subprocess enters a pathological state (fd exhaustion,
asyncio selector spin) it can emit tens of thousands of log lines per
second; the parent's IPC reader faithfully re-emits every one, so a
single sick daemon maxes out a CPU core on the parent and floods the
log ring.  Forwarded *log* envelopes are therefore token-bucket
limited; status and error envelopes are never dropped.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

from sendspin_bridge.services.ipc.subprocess_ipc import SubprocessIpcService


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _make_service(clock: _Clock, **kwargs):
    logger = MagicMock(spec=logging.Logger)
    service = SubprocessIpcService(
        player_name="TestSpeaker",
        protocol_warning_cache=set(),
        status_updater=MagicMock(),
        logger_=logger,
        log_clock=clock,
        **kwargs,
    )
    return service, logger


def _log_msg(text: str = "spam") -> dict:
    return {"type": "log", "level": "info", "msg": text}


def test_logs_under_the_limit_are_all_forwarded():
    clock = _Clock()
    service, logger = _make_service(clock)
    for i in range(20):
        clock.now = i * 1.0  # one line per second — far below any limit
        service.handle_message(_log_msg(f"line {i}"))
    assert logger.info.call_count == 20


def test_log_storm_is_suppressed_not_forwarded():
    clock = _Clock()
    service, logger = _make_service(clock, log_rate_per_s=50.0, log_burst=100.0)
    # 10_000 lines within the same millisecond — the #345 storm shape.
    for i in range(10_000):
        service.handle_message(_log_msg(f"storm {i}"))
    # Only the burst allowance passes; the rest are dropped.
    assert logger.info.call_count <= 100
    assert logger.info.call_count < 10_000


def test_suppression_summary_emitted_on_recovery():
    clock = _Clock()
    service, logger = _make_service(clock, log_rate_per_s=50.0, log_burst=10.0)
    for i in range(1_000):
        service.handle_message(_log_msg(f"storm {i}"))
    suppressed_before_recovery = 1_000 - logger.info.call_count
    assert suppressed_before_recovery > 0

    # Storm over; a second later the next line must pass AND carry a
    # one-line summary of what was dropped.
    clock.now = 1.0
    service.handle_message(_log_msg("calm"))
    assert any("uppressed" in str(c.args[0]) for c in logger.warning.call_args_list), (
        "expected a suppression summary warning after recovery"
    )


def test_status_and_error_envelopes_bypass_the_gate():
    clock = _Clock()
    status_updater = MagicMock()
    logger = MagicMock(spec=logging.Logger)
    service = SubprocessIpcService(
        player_name="TestSpeaker",
        protocol_warning_cache=set(),
        status_updater=status_updater,
        logger_=logger,
        log_clock=clock,
        log_rate_per_s=50.0,
        log_burst=10.0,
        allowed_keys=frozenset({"playing"}),
    )
    # Exhaust the log budget completely.
    for i in range(1_000):
        service.handle_message(_log_msg(f"storm {i}"))

    status_updater.reset_mock()
    service.handle_message({"type": "status", "playing": True})
    status_updater.assert_called_once_with({"playing": True})

    status_updater.reset_mock()
    service.handle_message({"type": "error", "message": "daemon crashed", "details": {"at": "now"}})
    status_updater.assert_called_once()
    logger.error.assert_called()
