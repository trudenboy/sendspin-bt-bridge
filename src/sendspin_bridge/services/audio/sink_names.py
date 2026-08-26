"""What an audio server calls a Bluetooth speaker, read and written in one place.

PipeWire, WirePlumber and PulseAudio each name the same speaker differently,
so the bridge tries several spellings when it looks for a sink and has to
recognise any of them when one shows up in an event.  Those two halves used to
be written separately — a list of f-strings in the connect path, a regex in
the sink monitor — and had already drifted: the raw-colon name WirePlumber
publishes on Ubuntu 26.04 (issue #314) is one the bridge itself selects, and
the reader did not know it.

A name the bridge can choose must be a name the bridge can read, so both come
from here.
"""

from __future__ import annotations

import re

from sendspin_bridge.bluetooth.address import DeviceAddress

__all__ = ["address_from_sink_name", "is_bluez_sink_name", "sink_name_candidates"]

#: Either audio server's prefix, then the address in either spelling.  The
#: name may carry a profile or index suffix, or nothing at all.
_SINK_NAME_RE = re.compile(
    r"^bluez_(?:sink|output)\."
    r"(?P<address>[0-9A-Fa-f]{2}(?:[:_][0-9A-Fa-f]{2}){5})"
    r"(?:\..*)?$"
)


def sink_name_candidates(address: DeviceAddress) -> list[str]:
    """Every name this speaker's sink might have, most specific first.

    Order matters: the bare forms match a device that has published nothing
    else yet, so a host that offers both must resolve to the specific one.
    """
    underscored = address.underscores
    return [
        f"bluez_output.{underscored}.1",  # PipeWire
        f"bluez_output.{underscored}.a2dp-sink",
        f"bluez_sink.{underscored}.a2dp_sink",  # PulseAudio
        f"bluez_sink.{underscored}",
        # WirePlumber on Ubuntu 26.04 publishes the address unmodified;
        # last, so the spellings above win wherever both exist (issue #314).
        f"bluez_output.{address.colons}",
    ]


def address_from_sink_name(sink_name: object) -> DeviceAddress | None:
    """The speaker a sink name refers to, or ``None`` if it names none."""
    if not isinstance(sink_name, str):
        return None
    match = _SINK_NAME_RE.match(sink_name.strip())
    if not match:
        return None
    return DeviceAddress.parse(match.group("address"))


def is_bluez_sink_name(sink_name: object) -> bool:
    """Whether this name belongs to a Bluetooth speaker at all."""
    return address_from_sink_name(sink_name) is not None
