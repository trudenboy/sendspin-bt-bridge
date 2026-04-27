"""Per-MAC last-activity tracker for inbound AVRCP source correlation.

BlueZ's AVRCP TG → MPRIS forwarding strips source-CT identity: when a
speaker presses Play/Pause/Next, BlueZ dispatches the method call to
whichever MPRIS player it picked as the addressed player on the adapter
(``profiles/audio/avrcp.c:target_init`` always picks
``server->players[0]``).  The forwarded D-Bus message carries no field
identifying which connected speaker actually pressed the button.

We sidestep this by subscribing to ``org.bluez.MediaPlayer1.PropertiesChanged``
on the per-device path ``/org/bluez/hciN/dev_<MAC>/playerN`` (BlueZ
creates this object per connected speaker as part of the AVRCP TG ↔ CT
exchange).  When a speaker's button press triggers a Status update on
its own MediaPlayer1, we record ``(mac, monotonic_ts)`` here.  The
inbound MPRIS dispatch then queries ``get_recent_active()`` within a
short time window to recover the source MAC and dispatch to the right
client.

Trade-offs:

* Speaker firmware must emit MediaPlayer1.PropertiesChanged near the
  AVRCP passthrough.  Most do for Play/Pause/Status; some are silent on
  Next/Previous (those rely on the streaming-fallback in the dispatch
  resolver).
* When two speakers emit Status updates within the same window, the
  most-recent wins — adequate for hands-on use, may mis-route under
  rapid concurrent button-mashing on multiple devices.

This module is pure state — D-Bus subscription is layered on top by
``services/device_activation.py`` so the tracker stays exercisable from
tests without requiring a live system bus.
"""

from __future__ import annotations

import threading
import time

# Default correlation window in seconds.  Empirically sufficient for the
# speaker firmware tested on VM 105 (ENEBY 20, WH-1000XM4); same value
# scyto's ha-bluetooth-audio-manager uses for the same workaround.
DEFAULT_CORRELATION_WINDOW_S = 2.0


class AvrcpSourceTracker:
    """Per-MAC monotonic-timestamp store with thread-safe access.

    Lifecycle: ``note_activity`` runs from BT-manager subscription
    threads (one per connected device), ``get_recent_active`` runs from
    the asyncio loop (inbound MPRIS dispatch), and ``clear`` runs from
    the disconnect hook.  All three contend for the same internal dict;
    ``_lock`` serialises them.
    """

    def __init__(self) -> None:
        self._last: dict[str, float] = {}  # uppercased MAC → monotonic ts
        self._lock = threading.Lock()

    def note_activity(self, mac: str, *, now: float | None = None) -> None:
        """Record that *mac* just emitted MediaPlayer1.PropertiesChanged.

        Empty MAC is silently ignored — a defensive guard against bad
        callers passing through ``getattr(x, 'mac', '')`` results that
        shouldn't pollute the lookup table with a meaningless key.
        """
        if not mac:
            return
        ts = now if now is not None else time.monotonic()
        key = mac.upper()
        with self._lock:
            self._last[key] = ts

    def get_recent_active(
        self,
        *,
        window_s: float = DEFAULT_CORRELATION_WINDOW_S,
        now: float | None = None,
    ) -> str | None:
        """Return the MAC whose last activity is within *window_s* seconds.

        When multiple MACs are within the window, the most-recent wins —
        matches the user-intent assumption that the freshest speaker
        Status update came from the speaker the user just touched.
        """
        ts_now = now if now is not None else time.monotonic()
        cutoff = ts_now - window_s
        with self._lock:
            recent = [(mac, ts) for mac, ts in self._last.items() if ts >= cutoff]
        if not recent:
            return None
        recent.sort(key=lambda kv: kv[1], reverse=True)
        return recent[0][0]

    def clear(self, mac: str) -> None:
        """Forget any activity for *mac*.

        Called from the disconnect hook so a stale recent-activity record
        for a now-gone device doesn't mis-route a subsequent button press
        from another speaker.  Tolerant of unknown MACs (disconnect hook
        may fire twice).
        """
        if not mac:
            return
        key = mac.upper()
        with self._lock:
            self._last.pop(key, None)


_TRACKER = AvrcpSourceTracker()


def get_tracker() -> AvrcpSourceTracker:
    """Return the process-wide AvrcpSourceTracker singleton.

    Shared across:
      - ``services/device_activation.py`` (writes from PropertiesChanged
        subscriptions, clears on disconnect)
      - the inbound MPRIS dispatch resolver (reads to correlate the
        anonymous BlueZ-forwarded method call to a source MAC)
    """
    return _TRACKER
