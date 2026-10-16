"""Playout settings: config keys (web UI) that environment variables override."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

COMPENSATE_ENV = "SENDSPIN_COMPENSATE_OUTPUT_LATENCY"
PLAYOUT_DIAG_ENV = "SENDSPIN_PLAYOUT_DIAG"


def env_flag(name: str, *, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() not in ("0", "false", "no", "off")


def output_latency_compensated(config: Mapping[str, Any]) -> bool:
    return env_flag(COMPENSATE_ENV, default=bool(config.get("COMPENSATE_OUTPUT_LATENCY", True)))
