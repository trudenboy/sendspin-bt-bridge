"""Link-role inference: a role is claimed only with evidence, never from a name prefix."""

from __future__ import annotations

import pytest

from sendspin_bridge.services.hardware.roles import (
    DeviceLink,
    LinkEndpoint,
    RoleEvidence,
    SourceLink,
    infer_roles,
)

MAC = "FC:58:FA:EB:08:6C"
OTHER = "30:21:0E:0A:AE:5A"
UNDERSCORES = "FC_58_FA_EB_08_6C"


def _roles(*pairs: tuple[str, str]) -> list[RoleEvidence]:
    return [RoleEvidence(role=role, evidence=evidence) for role, evidence in pairs]


@pytest.mark.parametrize(
    ("label", "device", "endpoints", "source_feed", "expected"),
    [
        (
            "an active transport is the output",
            DeviceLink(MAC, transport_state="active"),
            [],
            None,
            _roles(("output", "transport:active")),
        ),
        (
            "an idle transport claims nothing",
            DeviceLink(MAC, transport_state="idle"),
            [],
            None,
            [],
        ),
        (
            "a running PulseAudio sink is the output",
            DeviceLink(MAC),
            [LinkEndpoint("sink", f"bluez_sink.{UNDERSCORES}.a2dp_sink", "running")],
            None,
            _roles(("output", f"sink:bluez_sink.{UNDERSCORES}.a2dp_sink")),
        ),
        (
            "a PipeWire sink name is the output",
            DeviceLink(MAC),
            [LinkEndpoint("sink", f"bluez_output.{UNDERSCORES}.1", "idle")],
            None,
            _roles(("output", f"sink:bluez_output.{UNDERSCORES}.1")),
        ),
        (
            "WirePlumber's colon spelling is the output",
            DeviceLink(MAC),
            [LinkEndpoint("sink", f"bluez_output.{MAC}", "idle")],
            None,
            _roles(("output", f"sink:bluez_output.{MAC}")),
        ),
        (
            "a suspended sink claims nothing",
            DeviceLink(MAC),
            [LinkEndpoint("sink", f"bluez_sink.{UNDERSCORES}.a2dp_sink", "suspended")],
            None,
            [],
        ),
        (
            "another speaker's sink claims nothing",
            DeviceLink(MAC),
            [LinkEndpoint("sink", "bluez_sink.30_21_0E_0A_AE_5A.a2dp_sink", "running")],
            None,
            [],
        ),
        (
            "a source named for the speaker is the input",
            DeviceLink(MAC),
            [LinkEndpoint("source", f"bluez_source.{UNDERSCORES}", "running")],
            None,
            _roles(("input", f"source:bluez_source.{UNDERSCORES}")),
        ),
        (
            "a suspended source claims nothing",
            DeviceLink(MAC),
            [LinkEndpoint("source", f"bluez_source.{UNDERSCORES}", "suspended")],
            None,
            [],
        ),
        (
            "a capture node named for the speaker is the input",
            DeviceLink(MAC),
            [LinkEndpoint("capture", f"bluez_input.{UNDERSCORES}.2", "")],
            None,
            _roles(("input", f"capture:bluez_input.{UNDERSCORES}.2")),
        ),
        (
            "the phone on the source feed is the input",
            DeviceLink(MAC),
            [],
            SourceLink(phone_mac=MAC),
            _roles(("input", f"source_feed:{MAC}")),
        ),
        (
            "capture evidence outranks the source feed",
            DeviceLink(MAC),
            [LinkEndpoint("capture", f"bluez_input.{UNDERSCORES}.2", "")],
            SourceLink(phone_mac=MAC),
            _roles(("input", f"capture:bluez_input.{UNDERSCORES}.2")),
        ),
        (
            "a multipoint headset holds both roles",
            DeviceLink(MAC, transport_state="active"),
            [LinkEndpoint("source", f"bluez_source.{UNDERSCORES}", "running")],
            None,
            _roles(("output", "transport:active"), ("input", f"source:bluez_source.{UNDERSCORES}")),
        ),
        (
            "an unrelated source claims nothing",
            DeviceLink(MAC),
            [LinkEndpoint("source", "alsa_input.usb-dac", "running")],
            None,
            [],
        ),
        (
            "a malformed bluez name claims nothing",
            DeviceLink(MAC),
            [LinkEndpoint("sink", "bluez_sink.not-a-mac", "running")],
            None,
            [],
        ),
        (
            "another phone on the source feed claims nothing",
            DeviceLink(MAC),
            [],
            SourceLink(phone_mac=OTHER),
            _roles(),
        ),
        (
            "a malformed device address cannot claim a role",
            DeviceLink("nope", transport_state="active"),
            [],
            None,
            [],
        ),
    ],
)
def test_infer_roles(label, device, endpoints, source_feed, expected):
    assert infer_roles(device, endpoints, source_feed) == expected, label


def test_infer_roles_is_pure_and_repeatable():
    """The same inputs give the same answer; the call mutates nothing."""
    device = DeviceLink(MAC, transport_state="active")
    endpoints = [LinkEndpoint("sink", f"bluez_sink.{UNDERSCORES}.a2dp_sink", "running")]
    first = infer_roles(device, endpoints, None)
    second = infer_roles(device, endpoints, None)
    assert first == second
    assert endpoints[0].state == "running"
