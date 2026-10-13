"""``GET /audio/sinks`` lists local outputs and says which player uses each."""

import json

import pytest

from sendspin_bridge.services.audio.sink_inventory import LocalSink


def _sink(name, **kw):
    base = {
        "name": name,
        "description": name,
        "state": "idle",
        "sample_spec": "s16le 2ch 48000Hz",
        "muted": False,
        "volume": 40,
        "active_port": "analog-output",
        "active_port_available": "available",
        "identifiers": {"serial": "S-" + name},
    }
    base.update(kw)
    return LocalSink(**base)


@pytest.fixture(autouse=True)
def _config(tmp_path, monkeypatch):
    import sendspin_bridge.config as config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "CONFIG_SCHEMA_VERSION": 6,
                "players": [
                    {"id": "dac-1", "name": "DAC", "backend": {"type": "pulse_sink", "sink": "alsa_output.usb-dac"}},
                ],
            }
        )
    )


def test_lists_sinks_with_in_use_by(monkeypatch):
    import sendspin_bridge.application.audio as audio
    from tests.support.api_client import make_client

    async def fake():
        return [_sink("alsa_output.usb-dac"), _sink("alsa_output.pci-hdmi")]

    monkeypatch.setattr(audio, "list_local_sinks", fake)
    body = make_client().get("/api/v1/audio/sinks").json()
    by_name = {s["name"]: s for s in body}
    assert by_name["alsa_output.usb-dac"]["in_use_by"] == "dac-1"
    assert by_name["alsa_output.pci-hdmi"]["in_use_by"] is None
    assert by_name["alsa_output.pci-hdmi"]["identifiers"] == {"serial": "S-alsa_output.pci-hdmi"}
    assert set(by_name["alsa_output.usb-dac"]) >= {
        "name",
        "description",
        "state",
        "sample_spec",
        "muted",
        "volume",
        "active_port_available",
        "identifiers",
    }


def test_inventory_excludes_bluez_and_bridge_sinks(monkeypatch):
    """The real inventory filter is applied (pactl output parsed, not mocked past it)."""
    import sendspin_bridge.application.audio as audio
    from sendspin_bridge.services.audio import sink_inventory
    from tests.support.api_client import make_client

    async def all_sinks():
        return [_sink("bluez_sink.AA.a2dp_sink"), _sink("sendspin_fallback"), _sink("alsa_output.x")]

    monkeypatch.setattr(sink_inventory, "_list_all_sinks", all_sinks)
    assert audio.list_local_sinks is sink_inventory.list_local_sinks
    names = [s["name"] for s in make_client().get("/api/v1/audio/sinks").json()]
    assert names == ["alsa_output.x"]
