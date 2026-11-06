"""Record one audio input for a sync measurement — borrowed, never selected.

The group-sync wizard can listen through any input the bridge knows: a local line-in, a headset's
HFP microphone, a phone streaming to the bridge.  The bridge records that input itself while the
sequence plays, so the operator does not need a second device with a microphone.

The input is *borrowed*: this module opens the source node directly (``pw-record --target`` /
``parecord --device``) and nothing else.  It never selects a source input, changes a card profile,
touches a sink, or talks to the source client — a device the source feed is already capturing is
refused by the use case before the recorder is ever started.

The recorder writes raw s16le mono at :data:`audio_decode.DECODE_RATE` (the rate the phone decode
also produces), so the bytes go through the same :func:`audio_decode.decode_to_mono` path and the
same estimator window as a phone upload.  It blocks on the child process: callers run it on a
worker thread, never on the bridge loop.
"""

from __future__ import annotations

import shutil
import struct
import subprocess
import threading

from sendspin_bridge.sync_tuning.audio_decode import DECODE_RATE

__all__ = [
    "CHANNELS",
    "RecorderUnavailable",
    "RunningCapture",
    "capture_argv",
    "open_capture",
    "recorder_tool",
    "run_capture",
    "to_wav",
]

#: Mono: the estimator compares one member's clicks; a stereo capture would only cost bytes.
CHANNELS = 1
SAMPLE_BYTES = 2
#: After its time is up the recorder gets this long to flush before it is killed.
STOP_GRACE_S = 5.0


class RecorderUnavailable(Exception):
    """The node could not be recorded: no recorder tool, a refused node, or no audio at all."""


def recorder_tool(capture_kind: str = "unknown") -> str:
    """The recorder of the host's audio server (``pw-record`` / ``parecord``).

    The source inputs' read model already classifies the server; with no answer (no inventory)
    the installed tools decide.
    """
    if capture_kind == "pipewire":
        return "pw-record"
    if capture_kind == "pulseaudio":
        return "parecord"
    for tool in ("pw-record", "parecord"):
        if shutil.which(tool):
            return tool
    raise RecorderUnavailable("Neither pw-record nor parecord is installed on this host")


def capture_argv(node: str, *, tool: str | None = None, rate: int = DECODE_RATE, channels: int = CHANNELS) -> list[str]:
    """The recorder's argv: open *node* itself, raw s16le mono on stdout, and nothing else."""
    device = str(node or "").strip()
    if not device:
        raise RecorderUnavailable("The input has no capture node")
    chosen = tool or recorder_tool()
    if chosen == "pw-record":
        return [
            "pw-record",
            "--target",
            device,
            "--rate",
            str(rate),
            "--channels",
            str(channels),
            "--format",
            "s16",
            "-",
        ]
    if chosen == "parecord":
        return [
            "parecord",
            "--raw",
            "--device",
            device,
            "--rate",
            str(rate),
            "--channels",
            str(channels),
            "--format",
            "s16le",
        ]
    raise RecorderUnavailable(f"{chosen} is not a recorder this bridge knows")


def run_capture(argv: list[str], seconds: float) -> bytes:
    """Run the recorder for *seconds* and return its raw s16le stdout.

    A child that refuses the node, dies early or records nothing is ``RecorderUnavailable``; the
    normal end is killing it once its time is up, so the timeout is not a failure by itself.
    """
    try:
        proc: subprocess.Popen[bytes] = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except (FileNotFoundError, PermissionError, OSError) as exc:
        raise RecorderUnavailable(f"{argv[0]} could not start: {exc}") from exc
    timed_out = False
    out = b""
    err = b""
    try:
        try:
            out, err = proc.communicate(timeout=seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            proc.terminate()
            try:
                out, err = proc.communicate(timeout=STOP_GRACE_S)
            except subprocess.TimeoutExpired:
                proc.kill()
                out, err = proc.communicate()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()
    if not timed_out and proc.returncode not in (0, None):
        reason = err.decode("utf-8", "replace").strip().splitlines()
        detail = f": {reason[-1][:200]}" if reason else ""
        raise RecorderUnavailable(f"{argv[0]} refused the input (exit {proc.returncode}){detail}")
    if not out:
        raise RecorderUnavailable(f"{argv[0]} recorded nothing from the input")
    return out


class RunningCapture:
    """A recorder that runs until it is stopped: the sequence plays while it records.

    ``run_capture`` records for a length fixed in advance, which a sequence started first and
    timed by Music Assistant's round trips can outrun; this one is opened before the sequence
    starts and stopped once it has finished, so the whole sequence is in the file.
    """

    def __init__(self, argv: list[str]) -> None:
        self._argv = argv
        try:
            self._proc: subprocess.Popen[bytes] = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except (FileNotFoundError, PermissionError, OSError) as exc:
            raise RecorderUnavailable(f"{argv[0]} could not start: {exc}") from exc
        self._out = bytearray()
        self._err = b""
        self._got_audio = threading.Event()
        self._reader = threading.Thread(target=self._read, name="sync-input-recorder", daemon=True)
        self._reader.start()

    def _read(self) -> None:
        stdout = self._proc.stdout
        assert stdout is not None
        while chunk := stdout.read1(65536):
            self._out.extend(chunk)
            self._got_audio.set()
        self._got_audio.set()  # ended: whoever waits for audio stops waiting

    def wait_for_audio(self, timeout: float) -> bool:
        """True once the recorder delivers samples; False when it ended, or delivered none in time."""
        self._got_audio.wait(timeout)
        return bool(self._out)

    def stop(self) -> bytes:
        """Stop the recorder and return everything it recorded."""
        proc = self._proc
        # its output closed (the reader finished) or it was reaped: it ended before being asked to
        exited_by_itself = not self._reader.is_alive() or proc.poll() is not None
        if not exited_by_itself:
            proc.terminate()
        try:
            proc.wait(timeout=STOP_GRACE_S)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        self._reader.join(timeout=STOP_GRACE_S)
        if proc.stderr is not None:
            self._err = proc.stderr.read() or b""
        if exited_by_itself and proc.returncode not in (0, None) and not self._out:
            reason = self._err.decode("utf-8", "replace").strip().splitlines()
            detail = f": {reason[-1][:200]}" if reason else ""
            raise RecorderUnavailable(f"{self._argv[0]} refused the input (exit {proc.returncode}){detail}")
        if not self._out:
            raise RecorderUnavailable(f"{self._argv[0]} recorded nothing from the input")
        return bytes(self._out)


def open_capture(argv: list[str]) -> RunningCapture:
    """Start the recorder; it records until ``stop()``."""
    return RunningCapture(argv)


def to_wav(pcm: bytes, *, rate: int = DECODE_RATE, channels: int = CHANNELS) -> bytes:
    """The recorder's raw s16le as a WAV file, the container the phone decode path reads."""
    block_align = SAMPLE_BYTES * channels
    data = pcm[: len(pcm) - len(pcm) % block_align]
    return (
        b"RIFF"
        + struct.pack("<I", 36 + len(data))
        + b"WAVE"
        + b"fmt "
        + struct.pack("<IHHIIHH", 16, 1, channels, rate, rate * block_align, block_align, SAMPLE_BYTES * 8)
        + b"data"
        + struct.pack("<I", len(data))
        + data
    )
