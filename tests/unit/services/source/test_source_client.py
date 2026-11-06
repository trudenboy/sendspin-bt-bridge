"""The source client: pairing with Music Assistant, capture lifecycle and the signal."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from sendspin_bridge.services.source import client as client_module
from sendspin_bridge.services.source import state as source_state
from sendspin_bridge.services.source.capture import CaptureEndpoint, chunk_bytes


class FakeStore:
    def __init__(self, paired: bool = False) -> None:
        self.psk = SimpleNamespace(category=_LONG_TERM) if paired else None
        self.records = [SimpleNamespace(psk_id="psk-1")] if paired else []
        self.code: str | None = None
        self.config = ClientPairingConfig()

    async def pairing_psk(self):
        return self.psk

    async def static_pairing_code(self):
        return self.code

    async def set_static_pairing_code(self, code: str) -> None:
        self.code = code

    async def get_pairing_config(self):
        return self.config

    async def store_pairing_config(self, config) -> None:
        self.config = config

    async def clear_pairing_psk(self) -> None:
        self.psk = None

    async def clear_static_pairing_code(self) -> None:
        self.code = None

    async def list_records(self):
        return list(self.records)

    async def remove_record(self, psk_id: str) -> None:
        self.records = [record for record in self.records if record.psk_id != psk_id]


from aiosendspin.noise.trust_store import ClientPairingConfig, PskCategory  # noqa: E402

_LONG_TERM = PskCategory.LONG_TERM


class FakeCapture:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False
        self.fed: list[bytes] = []
        self.timestamps: list[int | None] = []
        self._feed_gate = asyncio.Event()
        self._stop = asyncio.Event()

    async def start(self) -> None:
        self.started = True

    async def feed(self, chunk: bytes, capture_timestamp_us: int | None = None) -> None:
        self.fed.append(chunk)
        self.timestamps.append(capture_timestamp_us)
        if len(self.fed) >= 2:
            self._feed_gate.set()

    async def stop(self) -> None:
        self.stopped = True
        self._stop.set()


class FakeClient:
    def __init__(self, *, paired: bool = False, fail_connect: bool = False, start_error: str | None = None) -> None:
        self.paired = paired
        self.fail_connect = fail_connect
        self.start_error = start_error
        self.connected = False
        self.captures: list[FakeCapture] = []
        self.pairing_window_open = False
        self.command_listener = None
        self.disconnect_listener = None
        self.noise_psk = SimpleNamespace(category=_LONG_TERM) if paired else None
        self.now_us_value = 5_000_000

    async def connect(self, _url: str) -> None:
        if self.fail_connect:
            raise OSError("connection refused")
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    def add_server_command_listener(self, callback) -> None:
        self.command_listener = callback

    def add_disconnect_listener(self, callback) -> None:
        self.disconnect_listener = callback

    def open_pairing_window(self) -> None:
        self.pairing_window_open = True

    def close_pairing_window(self) -> None:
        self.pairing_window_open = False

    async def cancel_pairing(self) -> None:
        self.pairing_window_open = False

    def create_source_capture(self, _audio_format):
        if self.start_error:
            raise RuntimeError(self.start_error)
        capture = FakeCapture()
        self.captures.append(capture)
        return capture

    def now_us(self) -> int:
        return self.now_us_value

    def fire_disconnect(self) -> None:
        self.connected = False
        assert self.disconnect_listener is not None
        self.disconnect_listener()

    def fire_command(self, command: str) -> None:
        assert self.command_listener is not None
        self.command_listener(SimpleNamespace(source=SimpleNamespace(command=command)))


class FakeSignal:
    def __init__(self) -> None:
        self.reports: list[bool] = []
        self.accepted = True

    async def report(self, present: bool) -> bool:
        if self.accepted:
            self.reports.append(present)
        return self.accepted


class FakeBackend:
    name = "fake"

    def __init__(self) -> None:
        self.endpoint: CaptureEndpoint | None = None
        self.stream = FakeStream()
        self.nudged: list[str | None] = []
        self.preferred_mac: str | None = None

    def set_preferred_mac(self, mac: str | None) -> None:
        self.preferred_mac = str(mac or "").upper() or None

    async def find(self):
        if self.preferred_mac and (self.endpoint is None or self.endpoint.phone_mac != self.preferred_mac):
            return None
        return self.endpoint

    async def open(self, _endpoint):
        return self.stream

    async def ensure_a2dp_source(self, mac=None):
        self.nudged.append(mac)
        return True


class FakeStream:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False

    @property
    def running(self) -> bool:
        return self.started and not self.stopped

    async def start(self) -> None:
        self.started = True

    async def chunks(self):
        while not self.stopped:
            yield b"\x00" * chunk_bytes()
            await asyncio.sleep(0.001)

    async def stop(self) -> None:
        self.stopped = True


async def _wait_for(predicate, timeout: float = 2.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.005)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    source_state.reset()
    monkeypatch.setattr(client_module, "WATCH_INTERVAL_S", 0.01)
    monkeypatch.setattr(client_module, "PAIRING_POLL_S", 0.01)
    yield
    source_state.reset()


@pytest.fixture
def harness(monkeypatch, tmp_path):
    store = FakeStore()
    identity = SimpleNamespace(peer_id="peer")
    monkeypatch.setattr(client_module, "load_or_create_identity", lambda _dir: identity)
    monkeypatch.setattr(client_module, "open_pairing_store", lambda _dir: _async(store))

    fake_client = FakeClient()
    signal = FakeSignal()
    backend = FakeBackend()
    created: list[FakeClient] = []

    def factory(_settings, _identity, _store):
        created.append(fake_client)
        return fake_client

    settings = client_module.SourceSettings(server_url="ws://ma:8927/sendspin", state_dir=tmp_path)
    client = client_module.SourceClient(
        settings,
        backend,
        client_factory=factory,
        signal_factory=lambda _client: signal,
    )
    return SimpleNamespace(client=client, fake=fake_client, store=store, signal=signal, backend=backend)


async def _async(value):
    return value


async def test_use_device_selects_the_streaming_device(harness):
    await harness.client.start()
    mac = "34:C7:E9:25:A3:A3"
    harness.backend.endpoint = CaptureEndpoint(
        node="bluez_source.34_C7_E9_25_A3_A3", backend="fake", phone_mac=mac, running=True
    )

    await harness.client.use_device(mac)

    assert harness.backend.preferred_mac == mac
    assert source_state.snapshot().phone_mac == mac


async def test_use_device_refuses_a_device_that_is_not_streaming(harness):
    await harness.client.start()
    harness.backend.endpoint = None

    with pytest.raises(client_module.SourceError) as error:
        await harness.client.use_device("34:C7:E9:25:A3:A3")

    assert error.value.code == "device_not_streaming"
    assert harness.backend.preferred_mac is None


async def test_set_target_plays_the_live_input_when_streaming(harness, monkeypatch):
    await harness.client.start()
    harness.client._stream = object()
    calls: list[tuple[str, str, str]] = []

    async def fake_play(url, token, queue_id, uri):
        calls.append((url, queue_id, uri))

    monkeypatch.setattr("sendspin_bridge.services.source.target.play_source_on", fake_play)
    monkeypatch.setattr(
        "sendspin_bridge.services.music_assistant.ma_runtime_state.get_ma_api_credentials",
        lambda: ("http://ma:8095", "token"),
    )

    error = await harness.client.set_target("kitchen")

    assert error is None
    assert calls == [("http://ma:8095", "kitchen", "sendspin_source://audio_source/peer")]
    snapshot = source_state.snapshot()
    assert snapshot.target_player == "kitchen"
    assert snapshot.target_error is None


async def test_a_failed_target_play_keeps_the_choice_and_reports_it(harness, monkeypatch):
    await harness.client.start()
    harness.client._stream = object()

    async def fail(*_args):
        raise OSError("MA is down")

    monkeypatch.setattr("sendspin_bridge.services.source.target.play_source_on", fail)
    monkeypatch.setattr(
        "sendspin_bridge.services.music_assistant.ma_runtime_state.get_ma_api_credentials",
        lambda: ("http://ma:8095", "token"),
    )

    error = await harness.client.set_target("kitchen")

    assert error == "target_unreachable"
    assert source_state.snapshot().target_player == "kitchen"
    assert source_state.snapshot().target_error == "target_unreachable"


async def test_forget_device_removes_the_bond(harness, monkeypatch):
    removed: list[str] = []
    monkeypatch.setattr("sendspin_bridge.services.bluetooth.bt_remove_device", removed.append)
    await harness.client.start()

    await harness.client.forget_device("34:C7:E9:25:A3:A3")

    assert removed == ["34:C7:E9:25:A3:A3"]


async def test_start_shows_a_pairing_code_when_no_psk_exists(harness):
    await harness.client.start()

    snapshot = source_state.snapshot()
    assert snapshot.enabled is True
    assert snapshot.state == "pairing"
    assert snapshot.ma_paired is False
    assert snapshot.ma_pairing_code and len(snapshot.ma_pairing_code) == 8
    assert harness.fake.pairing_window_open is True
    assert harness.store.code == snapshot.ma_pairing_code

    await harness.client.stop()


async def test_start_connects_without_a_window_when_already_paired(harness):
    harness.store.psk = SimpleNamespace(category=_LONG_TERM)
    harness.store.records = [SimpleNamespace(psk_id="psk-1")]

    await harness.client.start()
    await _wait_for(lambda: source_state.snapshot().ma_connected)

    snapshot = source_state.snapshot()
    assert snapshot.ma_paired is True
    assert snapshot.ma_pairing_code is None
    assert harness.fake.pairing_window_open is False

    await harness.client.stop()


async def test_a_failed_connect_is_reported_as_ma_unreachable(harness):
    harness.fake.fail_connect = True

    await harness.client.start()
    await _wait_for(lambda: source_state.snapshot().ma_error == "ma_unreachable")

    assert source_state.snapshot().ma_connected is False

    await harness.client.stop()


async def test_the_servers_start_command_starts_the_capture(harness):
    harness.backend.endpoint = CaptureEndpoint(
        node="bluez_source.34_C7_E9_25_A3_A3", backend="fake", phone_mac="34:C7:E9:25:A3:A3"
    )
    await harness.client.start()
    await _wait_for(lambda: source_state.snapshot().ma_connected)
    await _wait_for(lambda: source_state.snapshot().phone is not None)

    harness.fake.fire_command("start")
    await _wait_for(lambda: source_state.snapshot().streaming)

    assert harness.fake.captures[0].started is True
    assert harness.backend.stream.started is True
    await _wait_for(lambda: len(harness.fake.captures[0].fed) >= 2)
    assert harness.fake.captures[0].timestamps[0] == harness.fake.now_us_value - 10_000

    harness.fake.fire_command("stop")
    await _wait_for(lambda: harness.fake.captures[0].stopped)
    assert source_state.snapshot().streaming is False

    await harness.client.stop()


async def test_the_servers_start_also_opens_the_chosen_target(harness, monkeypatch):
    harness.backend.endpoint = CaptureEndpoint(
        node="bluez_source.34_C7_E9_25_A3_A3", backend="fake", phone_mac="34:C7:E9:25:A3:A3"
    )
    calls: list[tuple[str, str, str]] = []

    async def fake_play(url, token, queue_id, uri):
        calls.append((url, queue_id, uri))

    monkeypatch.setattr("sendspin_bridge.services.source.target.play_source_on", fake_play)
    monkeypatch.setattr(
        "sendspin_bridge.services.music_assistant.ma_runtime_state.get_ma_api_credentials",
        lambda: ("http://ma:8095", "token"),
    )
    await harness.client.set_target("kitchen")
    await harness.client.start()
    await _wait_for(lambda: source_state.snapshot().ma_connected)
    harness.fake.fire_command("start")
    await _wait_for(lambda: bool(calls))

    assert calls == [("http://ma:8095", "kitchen", "sendspin_source://audio_source/peer")]
    await harness.client.stop()


async def test_a_start_before_the_phone_appears_is_honored_once_it_does(harness):
    await harness.client.start()
    await _wait_for(lambda: source_state.snapshot().ma_connected)

    harness.fake.fire_command("start")
    await asyncio.sleep(0.05)
    assert harness.fake.captures == []

    harness.backend.endpoint = CaptureEndpoint(node="bluez_source.34_C7_E9_25_A3_A3", backend="fake", running=True)
    await _wait_for(lambda: source_state.snapshot().streaming)

    assert len(harness.fake.captures) == 1

    await harness.client.stop()


async def test_losing_music_assistant_stops_the_capture(harness):
    harness.backend.endpoint = CaptureEndpoint(node="bluez_source.34_C7_E9_25_A3_A3", backend="fake", running=True)
    await harness.client.start()
    await _wait_for(lambda: source_state.snapshot().ma_connected)
    harness.fake.fire_command("start")
    await _wait_for(lambda: source_state.snapshot().streaming)

    harness.fake.fire_disconnect()
    await _wait_for(lambda: harness.fake.captures[0].stopped)

    assert source_state.snapshot().streaming is False
    assert harness.fake.captures[0].stopped is True

    await harness.client.stop()


async def test_opening_the_pairing_window_twice_is_refused_when_already_paired(harness):
    harness.store.psk = SimpleNamespace(category=_LONG_TERM)
    harness.store.records = [SimpleNamespace(psk_id="psk-1")]
    await harness.client.start()
    await _wait_for(lambda: source_state.snapshot().ma_connected)

    with pytest.raises(client_module.SourceError) as raised:
        await harness.client.open_ma_pairing()

    assert raised.value.code == "ma_already_paired"

    await harness.client.stop()


async def test_forgetting_the_pairing_clears_the_psk_and_reopens_the_window(harness):
    harness.store.psk = SimpleNamespace(category=_LONG_TERM)
    harness.store.records = [SimpleNamespace(psk_id="psk-1")]
    await harness.client.start()
    await _wait_for(lambda: source_state.snapshot().ma_connected)

    await harness.client.cancel_ma_pairing(forget=True)

    assert harness.store.psk is None
    assert harness.store.records == []
    assert harness.store.code is not None
    assert source_state.snapshot().ma_paired is False
    assert source_state.snapshot().ma_pairing_code is not None

    await harness.client.stop()


async def test_stop_clears_the_state(harness):
    await harness.client.start()
    await _wait_for(lambda: source_state.snapshot().ma_connected)

    await harness.client.stop()

    snapshot = source_state.snapshot()
    assert snapshot.enabled is False
    assert snapshot.state == "off"
    assert snapshot.ma_connected is False


async def test_stop_closes_the_phone_pairing_window(harness, monkeypatch):
    from sendspin_bridge.services.source import pairing

    discoverable: list[bool] = []
    monkeypatch.setattr(pairing, "_task", None)
    monkeypatch.setattr(pairing, "_known_devices", set())
    monkeypatch.setattr(pairing, "_adapters", lambda: ["C0:FB:F9:62:D6:9D"])
    monkeypatch.setattr(pairing, "_devices", lambda: set())
    monkeypatch.setattr(pairing, "_set_discoverable", lambda _adapters, on: discoverable.append(on) or True)
    monkeypatch.setattr(pairing, "ensure_agent", lambda: _async(True))

    await harness.client.start()
    await _wait_for(lambda: source_state.snapshot().ma_connected)
    await pairing.open_window(30)
    assert source_state.snapshot().pairing_until > 0
    assert discoverable == [True]

    # The window exists to feed the source: stopping the source ends it.
    try:
        await harness.client.stop()

        snapshot = source_state.snapshot()
        assert snapshot.pairing_until == 0
        assert snapshot.pairing_adapters == ()
        assert discoverable[-1] is False
    finally:
        await pairing.close_window()


async def test_line_sense_follows_the_phone_and_is_deduplicated(harness):
    harness.backend.endpoint = CaptureEndpoint(node="bluez_source.34_C7_E9_25_A3_A3", backend="fake", running=True)
    await harness.client.start()
    await _wait_for(lambda: source_state.snapshot().ma_connected)
    await _wait_for(lambda: True in harness.signal.reports)
    reported = list(harness.signal.reports)

    await asyncio.sleep(0.05)
    assert harness.signal.reports == reported  # unchanged presence is not re-sent

    harness.backend.endpoint = None
    await _wait_for(lambda: harness.signal.reports[-1] is False)

    await harness.client.stop()


async def test_the_signal_adapter_reports_through_the_admitted_connection():
    from aiosendspin.models.types import SignalState

    sent: list[SignalState] = []

    class _Connection:
        async def send_source_signal(self, signal):
            sent.append(signal)

    class _Client:
        _admitted_connection = _Connection()

    adapter = client_module.AdmittedSignal(_Client())

    assert await adapter.report(True) is True
    assert sent == [SignalState.PRESENT]


async def test_the_signal_adapter_refuses_a_changed_private_shape():
    class _Client:
        _admitted_connection = object()

    adapter = client_module.AdmittedSignal(_Client())

    with pytest.raises(RuntimeError, match="send_source_signal"):
        await adapter.report(True)
