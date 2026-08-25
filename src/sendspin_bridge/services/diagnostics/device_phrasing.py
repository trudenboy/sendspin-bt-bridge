"""Sentences about a device that more than one screen has to agree on.

The recovery card and the operator-guidance card both describe how a
reconnect is going, and each carried its own copy of the sentence.  They read
different sources — one the normalised state, one the raw extras — and
punctuated differently, so the same speaker could be described two ways at
once, and given two different attempt counts whenever the two
representations disagreed.

A device's phrasing belongs in one place, next to the accessor that decides
where the numbers come from.
"""

from __future__ import annotations

from typing import Any

from sendspin_bridge.services.infrastructure._helpers import _device_extra

__all__ = ["device_bluetooth_state", "reconnect_attempt_summary"]


def device_bluetooth_state(device: Any) -> dict[str, Any]:
    """The device's Bluetooth facts, from whichever representation carries them.

    The normalised state is preferred where it exists; the raw extras are the
    fallback for callers that never built one.  Both are consulted here so a
    caller cannot accidentally read only one and disagree with its neighbour.
    """
    state_model = getattr(device, "state_model", None)
    if isinstance(device, dict):
        state_model = device.get("state_model")
    if isinstance(state_model, dict):
        bluetooth = state_model.get("bluetooth")
        if isinstance(bluetooth, dict) and bluetooth:
            return bluetooth
    return _device_extra(device)


def reconnect_attempt_summary(device: Any) -> str:
    """One sentence on the reconnect in flight, or ``""`` when there is none."""
    facts = device_bluetooth_state(device)
    attempt = int(facts.get("reconnect_attempt") or 0)
    if attempt <= 0:
        return ""
    threshold = int(facts.get("max_reconnect_fails") or 0)
    if threshold > 0:
        remaining = max(threshold - attempt, 0)
        return f"Reconnect attempt {attempt}/{threshold}. {remaining} attempts remain before auto-release."
    return f"Reconnect attempt {attempt} is in progress."
