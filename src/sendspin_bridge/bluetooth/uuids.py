"""The Bluetooth service UUIDs this bridge cares about.

A speaker advertises what it can do as a list of service UUIDs; the
bridge reads that list to tell an audio sink from a device that merely
paired. Kept apart from any transport so both the D-Bus and bluetoothctl
sides can name the same constant.
"""

from __future__ import annotations

__all__ = ["A2DP_SINK_UUID", "AUDIO_SINK_UUIDS", "HANDSFREE_UUID", "HEADSET_UUID"]

A2DP_SINK_UUID = "0000110b-0000-1000-8000-00805f9b34fb"
HANDSFREE_UUID = "0000111e-0000-1000-8000-00805f9b34fb"
HEADSET_UUID = "00001108-0000-1000-8000-00805f9b34fb"

# Any one of these advertised by the peer is enough to treat it as an audio
# device worth connecting. Used for the post-pair audio-profile sanity check.
AUDIO_SINK_UUIDS = frozenset({A2DP_SINK_UUID, HANDSFREE_UUID, HEADSET_UUID})
