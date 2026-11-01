"""The PipeWire capture: node discovery, command shape and the process lifecycle."""

from __future__ import annotations

import asyncio
import contextlib

from sendspin_bridge.services.source.capture import (
    CaptureEndpoint,
    CommandResult,
    ProcessCapture,
    chunk_bytes,
    mac_from_node,
    terminate_process,
)
from sendspin_bridge.services.source.capture.pipewire import (
    BLUEZ_INPUT_RE,
    PipeWireBackend,
    find_bluez_input,
)

WPCTL = """
PipeWire 'pipewire-0' [1.0.5, renso@HP-ProDesk, cookie:1]
 ├─ Sources:
 │      94. bluez_input.34_C7_E9_25_A3_A3.2
 │     107. alsa_input.pci-0000_00_1b.0.analog-stereo
"""


class _Proc:
    """A capture process with a preloaded reader."""

    def __init__(self, data: bytes = b"") -> None:
        self.stdout = asyncio.StreamReader()
        if data:
            self.stdout.feed_data(data)
        self.stdout.feed_eof()
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    async def wait(self) -> int:
        return int(self.returncode if self.returncode is not None else 0)


def test_the_phone_capture_node_is_found_by_name():
    assert find_bluez_input(WPCTL) == "bluez_input.34_C7_E9_25_A3_A3.2"


def test_no_bluetooth_capture_while_no_phone_streams():
    assert find_bluez_input("PipeWire 'pipewire-0'\n ├─ Sources:\n") is None
    assert find_bluez_input("bluez_output.6C_5C_3D_35_17_99.1") is None


def test_the_node_pattern_matches_what_pipewire_names():
    assert BLUEZ_INPUT_RE.fullmatch("bluez_input.6C_5C_3D_35_17_99.0") is not None
    assert BLUEZ_INPUT_RE.fullmatch("bluez_input.34:C7:E9:25:A3:A3.2") is None


def test_a_chunk_is_ten_milliseconds_of_s16_frames():
    assert chunk_bytes() == 48000 // 100 * 2 * 2  # 1920 bytes: 480 frames of stereo s16


def test_the_mac_is_decoded_from_the_node_name():
    assert mac_from_node("bluez_input.34_C7_E9_25_A3_A3.2") == "34:C7:E9:25:A3:A3"
    assert mac_from_node("bluez_source.FC_58_FA_EB_08_6C.a2dp_source") == "FC:58:FA:EB:08:6C"
    assert mac_from_node("alsa_input.pci") is None


async def test_the_backend_finds_the_node_and_opens_a_recording():
    seen: list[list[str]] = []

    async def run(argv, **_kwargs):
        seen.append(list(argv))
        return CommandResult(returncode=0, stdout=WPCTL)

    backend = PipeWireBackend(run=run)
    endpoint = await backend.find()

    assert endpoint == CaptureEndpoint(
        node="bluez_input.34_C7_E9_25_A3_A3.2",
        backend="pipewire",
        phone_mac="34:C7:E9:25:A3:A3",
        running=True,
    )
    stream = await backend.open(endpoint)
    assert isinstance(stream, ProcessCapture)
    assert seen == [["wpctl", "status"]]


async def test_the_recording_command_is_pw_record_of_the_node():
    captured: list[list[str]] = []

    async def spawn(*argv, **_kwargs):
        captured.append(list(argv))
        return _Proc()

    async def run(_argv, **_kwargs):
        return CommandResult(returncode=0, stdout=WPCTL)

    backend = PipeWireBackend(run=run, spawn=spawn)
    stream = await backend.open(
        CaptureEndpoint(node="bluez_input.34_C7_E9_25_A3_A3.2", backend="pipewire", running=True)
    )
    await stream.start()

    assert captured[0][:4] == ["pw-record", "--target", "bluez_input.34_C7_E9_25_A3_A3.2", "--rate"]
    assert captured[0][-1] == "-"


async def test_chunks_are_whole_frames_even_when_reads_are_partial():
    data = bytes(range(256)) * 16
    proc = _Proc(data)

    async def spawn(*_argv, **_kwargs):
        return proc

    stream = ProcessCapture(["pw-record"], spawn=spawn)
    await stream.start()
    chunks = [chunk async for chunk in stream.chunks()]

    assert all(len(chunk) == chunk_bytes() for chunk in chunks)
    assert b"".join(chunks) == data[: len(data) // chunk_bytes() * chunk_bytes()]


async def test_stop_terminates_and_then_kills_a_process_that_ignores_term():
    proc = await asyncio.create_subprocess_exec(
        "bash",
        "-c",
        "trap '' TERM; sleep 30",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        await terminate_process(proc, timeout=0.2)
        assert proc.returncode is not None
    finally:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        with contextlib.suppress(Exception):
            await proc.wait()
