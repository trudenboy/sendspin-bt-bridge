"""What a Bluetooth link is doing: ``output``, ``input``, or both.

The bridge is an A2DP *source* to a speaker and an A2DP *sink* for a phone, and
nothing in the transport records which way the audio flows.  The role is
therefore inferred from evidence — an active transport, an audio endpoint named
for the address, a capture node, the source feed's phone — and never from the
spelling of a sink name alone.  The name grammar lives in
:mod:`sendspin_bridge.services.audio.sink_names`; this module only asks it
"whose name is this?".

Pure: no I/O, no subprocess, no clock.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from sendspin_bridge.bluetooth.address import DeviceAddress
from sendspin_bridge.services.audio.sink_names import address_from_sink_name

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = [
    "CAPTURE_NAME_RE",
    "SOURCE_NAME_RE",
    "DeviceLink",
    "LinkEndpoint",
    "RoleEvidence",
    "SourceLink",
    "infer_roles",
]

Role = Literal["output", "input"]

OUTPUT: Role = "output"
INPUT: Role = "input"

#: PipeWire names the A2DP capture of a phone ``bluez_input.<MAC>.<n>``;
#: PulseAudio exports the same link as ``bluez_source.<MAC>``.  Both spell the
#: address the audio-server way, and ``DeviceAddress`` folds it.
CAPTURE_NAME_RE = re.compile(r"^bluez_input\.(?P<address>[0-9A-Fa-f_]{17})\.\d+$")
SOURCE_NAME_RE = re.compile(r"^bluez_source\.(?P<address>[0-9A-Fa-f_]{17})(?:\..*)?$")


@dataclass(frozen=True, slots=True)
class DeviceLink:
    """What role inference needs to know about the Bluetooth device."""

    mac: str
    transport_state: str | None = None


@dataclass(frozen=True, slots=True)
class LinkEndpoint:
    """One audio endpoint (or PipeWire capture node) as the audio server names it."""

    kind: str  # "sink" | "source" | "capture"
    name: str
    state: str = ""


@dataclass(frozen=True, slots=True)
class SourceLink:
    """The Sendspin source feed: the phone currently streaming into the bridge."""

    phone_mac: str | None = None


@dataclass(frozen=True, slots=True)
class RoleEvidence:
    """One claimed role and the fact that claims it."""

    role: Role
    evidence: str


def _address_from_name(name: str, pattern: re.Pattern[str]) -> DeviceAddress | None:
    match = pattern.match(name.strip())
    return DeviceAddress.parse(match.group("address")) if match else None


def _output_evidence(device: DeviceLink, endpoints: Iterable[LinkEndpoint], address: DeviceAddress) -> str | None:
    """An active transport first; then an audio sink named for the speaker."""
    if (device.transport_state or "").strip().lower() == "active":
        return "transport:active"
    for endpoint in endpoints:
        if endpoint.kind != "sink" or endpoint.state == "suspended":
            continue
        if address_from_sink_name(endpoint.name) == address:
            return f"sink:{endpoint.name}"
    return None


def _input_evidence(
    endpoints: Iterable[LinkEndpoint], source_feed: SourceLink | None, address: DeviceAddress
) -> str | None:
    """A capture node first; then a source; then the phone on the source feed."""
    source_evidence: str | None = None
    for endpoint in endpoints:
        if endpoint.kind == "capture":
            if _address_from_name(endpoint.name, CAPTURE_NAME_RE) == address:
                return f"capture:{endpoint.name}"
        elif endpoint.kind == "source" and source_evidence is None and endpoint.state != "suspended":
            if _address_from_name(endpoint.name, SOURCE_NAME_RE) == address:
                source_evidence = f"source:{endpoint.name}"
    if source_evidence is not None:
        return source_evidence
    if source_feed is not None and DeviceAddress.parse(source_feed.phone_mac) == address:
        return f"source_feed:{address.colons}"
    return None


def infer_roles(
    device_state: DeviceLink,
    endpoints: Iterable[LinkEndpoint],
    source_feed: SourceLink | None,
) -> list[RoleEvidence]:
    """The roles this link holds, each with the evidence that proves it.

    Output before input, so a device that does both renders in a stable order.
    """
    address = DeviceAddress.parse(device_state.mac)
    if address is None:
        return []
    roles: list[RoleEvidence] = []
    output = _output_evidence(device_state, endpoints, address)
    if output is not None:
        roles.append(RoleEvidence(role=OUTPUT, evidence=output))
    claimed_input = _input_evidence(endpoints, source_feed, address)
    if claimed_input is not None:
        roles.append(RoleEvidence(role=INPUT, evidence=claimed_input))
    return roles
