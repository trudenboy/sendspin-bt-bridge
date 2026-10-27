"""The Bluetooth service UUIDs this bridge cares about.

A speaker advertises what it can do as a list of service UUIDs; the
bridge reads that list to tell an audio sink from a device that merely
paired. Kept apart from any transport so both the D-Bus and bluetoothctl
sides can name the same constant.
"""

from __future__ import annotations

__all__ = ["A2DP_SINK_UUID", "A2DP_SOURCE_UUID", "AUDIO_SINK_UUIDS", "HANDSFREE_UUID", "HEADSET_UUID", "describe_uuids"]

A2DP_SINK_UUID = "0000110b-0000-1000-8000-00805f9b34fb"
A2DP_SOURCE_UUID = "0000110a-0000-1000-8000-00805f9b34fb"
HANDSFREE_UUID = "0000111e-0000-1000-8000-00805f9b34fb"
HEADSET_UUID = "00001108-0000-1000-8000-00805f9b34fb"

# Any one of these advertised by the peer is enough to treat it as an audio
# device worth connecting. Used for the post-pair audio-profile sanity check.
AUDIO_SINK_UUIDS = frozenset({A2DP_SINK_UUID, HANDSFREE_UUID, HEADSET_UUID})

# Names for the services a speaker commonly lists, for the connect-time log
# line that tells which roles a speaker offers (e.g. an Echo's Audio Source).
_SERVICE_NAMES = {
    "00001108": "Headset",
    "0000110a": "Audio Source",
    "0000110b": "Audio Sink",
    "0000110c": "AVRCP Target",
    "0000110d": "A2DP",
    "0000110e": "AVRCP",
    "0000110f": "AVRCP Controller",
    "00001112": "Headset AG",
    "0000111e": "Handsfree",
    "0000111f": "Handsfree AG",
    "00001200": "PnP Information",
}


def describe_uuids(uuids) -> str:
    """Return a readable list of *uuids*, naming the well-known services."""
    names = []
    for uuid in uuids:
        short = str(uuid).lower()[:8]
        names.append(_SERVICE_NAMES.get(short, str(uuid).lower()))
    return ", ".join(names) or "none"
