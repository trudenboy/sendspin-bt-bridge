"""Shared test support helpers (fakes, frozen transcripts)."""

from __future__ import annotations

from pathlib import Path

_TRANSCRIPTS_DIR = Path(__file__).parent / "transcripts"


def load_transcript(bluez_version: str, name: str) -> str:
    """Return a frozen real-world bluetoothctl transcript.

    Transcripts are captured on live hosts (see the capture table in
    ``tests/support/transcripts/``) and stored byte-for-byte, ANSI
    escapes included, so parser tests exercise the exact shapes each
    BlueZ version emits.
    """
    path = _TRANSCRIPTS_DIR / f"bluez-{bluez_version}" / f"{name}.txt"
    return path.read_text(encoding="utf-8", errors="replace")
