"""Startup-mute window must cover sendspin's sync-settle window (issue #341).

On every fresh stream start sendspin's ``AudioPlayer`` re-anchors and
runs a proportional drop/insert corrector that converges within
``_CORRECTION_TARGET_SECONDS``.  The repeated frame drops/duplications
during that window are audible as crackling / "sandy" noise.  The
daemon mutes the sink at startup and unmutes after
``_STARTUP_UNMUTE_DELAY_S`` — if that delay is shorter than the settle
window, the tail of the correction burst reaches the speaker.

The constant is read from the installed sendspin *source* via AST
rather than imported: sibling test modules stub ``sendspin.audio`` in
``sys.modules`` for the whole worker process, so a live import could
silently hand us a MagicMock instead of the real number.
"""

from __future__ import annotations

import ast
import importlib.metadata
from pathlib import Path

from sendspin_bridge.services.ipc import daemon_process

# Headroom above the corrector's convergence target: convergence is
# asymptotic (the corrector aims to fix the error *within* the target,
# small residual corrections continue slightly past it).
_SETTLE_HEADROOM_S = 0.5


def _read_sendspin_correction_target_seconds() -> float:
    audio_py = Path(str(importlib.metadata.distribution("sendspin").locate_file("sendspin/audio.py")))
    tree = ast.parse(audio_py.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "_CORRECTION_TARGET_SECONDS":
            assert node.value is not None
            return float(ast.literal_eval(node.value))
        if isinstance(node, ast.Assign) and any(
            getattr(t, "id", "") == "_CORRECTION_TARGET_SECONDS" for t in node.targets
        ):
            return float(ast.literal_eval(node.value))
    raise AssertionError(
        "sendspin renamed AudioPlayer._CORRECTION_TARGET_SECONDS — re-verify "
        "the sync-settle window and update this test (issue #341)"
    )


def test_startup_unmute_delay_covers_sendspin_correction_window():
    correction_target = _read_sendspin_correction_target_seconds()
    assert daemon_process._STARTUP_UNMUTE_DELAY_S >= correction_target + _SETTLE_HEADROOM_S, (
        "Startup unmute fires before sendspin's sync corrector settles: "
        f"unmute at {daemon_process._STARTUP_UNMUTE_DELAY_S}s, corrector "
        f"converges within {correction_target}s "
        "(issue #341 — crackling in the first seconds of playback)"
    )
