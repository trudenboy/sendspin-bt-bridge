"""Rebuild ``air_music.btsnoop.gz`` and ``air_music_truth.json.gz`` (air sensor simulation, Task A10).

A deterministic synthetic-music A2DP capture with a PCM-instant truth table, no copyrighted
material. :func:`music_pcm` renders 20 s of seeded pseudo-music (stereo S16LE, 44.1 kHz):

* 0–6 s percussive transients on a seeded 16th-note rhythm, with notes;
* 6–8 s a quiet passage (a sustained chord at −40 dBFS);
* 8–12 s a strict 4-on-the-floor loop (one bar rendered once and repeated sample for sample);
* 12–20 s mixed (rhythm, loop kick and notes).

Every section depends on the seed (tempo, hits, timbres), so the PCM of another seed is an
unrelated control.

The PCM is encoded with the system libsbc (the encoder BlueZ and PulseAudio use) through ctypes,
one 128-sample frame per call: 44.1 kHz, 8 sub-bands, 16 blocks, joint stereo, loudness,
bitpool 2 (scale factors, all the air envelope reads, are computed before bit allocation, so a
tiny bitpool keeps the file small without changing what is measured; with it a media packet
fits one ACL fragment, so the 310-byte fragmentation never splits one). Packing as PulseAudio's
bluez5 sink does: 7 frames per RTP packet (PT 96), ACL fragments of at most 310 bytes on handle
``HANDLE`` of controller ``INDEX``, a Connection Complete at 0 s, the packets sent every
``7·128/44100`` s with a seeded 0–1 ms lateness, and one Number Of Completed Packets per fragment
a seeded 1–9 ms after it.

Two disturbances:

* **PulseAudio "Skipping" at 9 s**, modelled as PA does it (its write index advances): the
  ``SKIP_SAMPLES`` (882 = 20 ms, not a whole number of frames) PCM samples after the last packet
  sent before 9 s are never encoded; the next packet's RTP timestamp advances by them while its
  sequence number stays contiguous and the send pacing continues.
* **AVDTP suspend at 14 s**: no packets for 1.5 s, then a new stream (fresh encoder, RTP
  timestamp and sequence number from 0, a new SSRC); the PCM of the gap is never encoded.

The truth (``air_music_truth.json.gz``) is recorded at the PCM instant, independently of the air
sensor: for every encoded SBC frame, the first PCM sample it consumed, the send time (the btsnoop
time of its packet's last ACL fragment, µs after the first record, so it includes the seeded 0–1 ms
send lateness) and its position in the packet.
A PCM sample ``p`` of frame ``(p0, t_send, pos)`` left at ``t_send + (pos·128 + p − p0)/rate``;
samples no frame consumed (the skip, the suspend, the tail) left never.

Built with libsbc ``LIBSBC_VERSION``; the gzip mtime is fixed, so a rebuild with that libsbc (and
the same libm) must reproduce ``EXPECTED_SHA256`` exactly. The script warns when the loaded libsbc
is another version, and an output whose hash differs is left as ``<name>.new`` (exit 1) without
touching the committed file.

    uv run python tests/fixtures/a2dp_air/build_air_music_fixture.py
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
import random
import struct
import sys
from array import array
from itertools import repeat
from operator import add, mul
from pathlib import Path

HERE = Path(__file__).resolve().parent
RATE = 44100
SEED = 20261028
SECONDS = 20.0
LIBSBC_VERSION = "1.3.1"
FRAME_SAMPLES = 128  # 8 sub-bands x 16 blocks
FRAMES_PER_PACKET = 7
ACL_FRAGMENT = 310
INDEX = 0
HANDLE = 0x002A
CID = 0x0041
SSRCS = (0x5EED0001, 0x5EED0002)
UNIX_T0 = 1_793_000_000.0  # unix time of the first record (2026-10-26)
FIRST_PACKET_S = 0.02
SKIP_AT_S = 9.0
SKIP_SAMPLES = 882  # 20 ms
SUSPEND_AT_S = 14.0
SUSPEND_S = 1.5
SEND_LATE_MAX_S = 0.001
NOCP_DELAY_S = (0.001, 0.009)
QUIET_DBFS = -40.0
SECTIONS = ((0.0, 6.0, "rhythm"), (6.0, 8.0, "quiet"), (8.0, 12.0, "loop"), (12.0, 20.0, "mixed"))
EXPECTED_SHA256 = {
    "air_music.btsnoop.gz": "9e0c0022a9d55727a8ed535b45ee586f889bfc95f9ef4840bfb1e7ed53250442",
    "air_music_truth.json.gz": "88cca9ca233a0e29d43a85736083ac24efd5f0f99b754e7dc640319244dad45c",
}

_MON_EVENT = 3  # MONITOR_EVENT_PKT
_MON_ACL_TX = 4  # MONITOR_ACL_TX_PKT
_BTSNOOP_EPOCH_DELTA_US = 0x00DCDDB30F2F8000


# ── the music ─────────────────────────────────────────────────────────────────────────────────


def _decay(n: int, tau_s: float, rate: int, attack_s: float = 0.0) -> list[float]:
    """An attack ramp of ``attack_s`` then ``exp(−t/τ)``, computed by repeated multiplication."""
    out = [0.0] * n
    k = math.exp(-1.0 / (tau_s * rate))
    na = min(n, round(attack_s * rate))
    for i in range(na):
        out[i] = (i + 1) / na
    v = 1.0
    for i in range(na, n):
        out[i] = v
        v *= k
    return out


def _noise(rng: random.Random, n: int, *, highpass: bool = False) -> list[float]:
    x = [rng.random() * 2.0 - 1.0 for _ in range(n + 1)]
    if highpass:
        return [0.5 * (x[i + 1] - x[i]) for i in range(n)]
    return x[:n]


def _instruments(rng: random.Random, rate: int) -> dict[str, list[list[float]]]:
    """Three seeded variants of each drum (mono, peak about 1)."""
    kit: dict[str, list[list[float]]] = {"kick": [], "snare": [], "hat": [], "clap": []}
    for _ in range(3):
        n = round(0.16 * rate)
        f0, f1 = 110.0 + 30.0 * rng.random(), 45.0
        env = _decay(n, 0.045, rate)
        click = _decay(round(0.003 * rate), 0.0008, rate)
        clicks = _noise(rng, len(click), highpass=True)
        phase, wave = 0.0, [0.0] * n
        for i in range(n):
            f = f1 + (f0 - f1) * math.exp(-i / (0.03 * rate))
            phase += 2.0 * math.pi * f / rate
            wave[i] = env[i] * math.sin(phase) + (0.6 * click[i] * clicks[i] if i < len(click) else 0.0)
        kit["kick"].append(wave)

        n = round(0.18 * rate)
        tone = 180.0 + 40.0 * rng.random()
        env, env_t = _decay(n, 0.05, rate), _decay(n, 0.03, rate)
        nz = _noise(rng, n, highpass=True)
        kit["snare"].append(
            [0.8 * env[i] * nz[i] + 0.4 * env_t[i] * math.sin(2.0 * math.pi * tone * i / rate) for i in range(n)]
        )

        n = round(0.05 * rate)
        env = _decay(n, 0.008 + 0.008 * rng.random(), rate)
        nz = _noise(rng, n, highpass=True)
        kit["hat"].append([env[i] * nz[i] for i in range(n)])

        n = round(0.12 * rate)
        nz = _noise(rng, n, highpass=True)
        env = [0.0] * n
        for burst in (0.0, 0.009, 0.019):
            b0 = round(burst * rate)
            for i, v in enumerate(_decay(n - b0, 0.006 if burst < 0.019 else 0.03, rate)):
                env[b0 + i] = max(env[b0 + i], v)
        kit["clap"].append([env[i] * nz[i] for i in range(n)])
    return kit


_TABLE_N = 4096


def _note_table(rng: random.Random) -> list[float]:
    """One cycle of a seeded harmonic wave (harmonics 1–8 with seeded amplitudes and phases)."""
    amps = [1.0 / h * (0.5 + rng.random()) for h in range(1, 9)]
    phases = [2.0 * math.pi * rng.random() for _ in range(8)]
    table = [
        sum(
            a * math.sin(2.0 * math.pi * h * i / _TABLE_N + ph)
            for h, (a, ph) in enumerate(zip(amps, phases, strict=True), 1)
        )
        for i in range(_TABLE_N)
    ]
    peak = max(abs(v) for v in table)
    return [v / peak for v in table]


def _note(table: list[float], freq: float, dur_s: float, rate: int, tau_s: float, attack_s: float) -> list[float]:
    n = round(dur_s * rate)
    env = _decay(n, tau_s, rate, attack_s)
    step = freq * _TABLE_N / rate
    mask = _TABLE_N - 1
    return [table[int(i * step) & mask] * env[i] for i in range(n)]


def _mix(dst_l: list[float], dst_r: list[float], at: int, wave: list[float], gain: float, pan: float) -> None:
    """Add ``wave`` at sample ``at`` with ``gain`` and a pan in −1…1 (equal-power-ish)."""
    n = min(len(wave), len(dst_l) - at)
    if n <= 0 or at < 0:
        return
    w = wave[:n]
    for dst, g in ((dst_l, gain * (1.0 - pan) * 0.5 + gain * 0.25), (dst_r, gain * (1.0 + pan) * 0.5 + gain * 0.25)):
        dst[at : at + n] = list(map(add, dst[at : at + n], map(mul, w, repeat(g))))


def music_pcm(seconds: float = SECONDS, rate: int = RATE, seed: int = SEED) -> bytes:
    """Deterministic pseudo-music, stereo S16LE (see the module docstring for the sections)."""
    rng = random.Random(seed)
    n = round(seconds * rate)
    left, right = [0.0] * n, [0.0] * n
    kit = _instruments(rng, rate)
    beat_s = 60.0 / (114.0 + 14.0 * rng.random())
    sixteenth = beat_s / 4.0
    scale = [0, 2, 4, 7, 9, 12, 14, 16, 19, 21]
    root = 48 + rng.randrange(12)
    tables = [_note_table(rng) for _ in range(3)]
    notes: dict[tuple[int, int, int], list[float]] = {}

    def note(key: int, table: int, dur16: int) -> list[float]:
        k = (key, table, dur16)
        if k not in notes:
            freq = 440.0 * 2.0 ** ((root + 12 + scale[key] - 69) / 12.0)
            notes[k] = _note(tables[table], freq, dur16 * sixteenth + 0.05, rate, 0.25, 0.004)
        return notes[k]

    def hit(name: str, t: float, gain: float, pan: float) -> None:
        _mix(left, right, round(t * rate), rng.choice(kit[name]), gain, pan)

    def rhythm(t0: float, t1: float, density: float) -> None:
        k = 0
        while (t := t0 + k * sixteenth) < t1:
            pos = k % 16
            if pos % 4 == 0 and rng.random() < 0.8:
                hit("kick", t, 0.55, 0.0)
            if pos in (4, 12) or rng.random() < 0.12 * density:
                hit("snare", t, 0.35 + 0.1 * rng.random(), rng.uniform(-0.3, 0.3))
            if rng.random() < 0.55 * density:
                hit("hat", t, 0.12 + 0.12 * rng.random(), rng.uniform(-0.6, 0.6))
            if rng.random() < 0.22 * density:
                dur16 = rng.choice((2, 3, 4, 6))
                _mix(
                    left,
                    right,
                    round(t * rate),
                    note(rng.randrange(len(scale)), rng.randrange(3), dur16),
                    0.18,
                    rng.uniform(-0.5, 0.5),
                )
            k += 1

    # 0-6 s: rhythm with notes.
    rhythm(0.0, 6.0, 1.0)
    # 6-8 s: a quiet chord at QUIET_DBFS (peak), slow attack and long decay: an almost flat envelope.
    quiet = 10.0 ** (QUIET_DBFS / 20.0)
    for key in (0, 2, 4):
        wave = _note(tables[0], 440.0 * 2.0 ** ((root + 12 + scale[key] - 69) / 12.0), 2.0, rate, 6.0, 0.15)
        _mix(left, right, round(6.0 * rate), wave, quiet / 3.0, 0.0)
    # 8-12 s: one bar rendered once, then repeated sample for sample.
    bar_n = round(4 * beat_s * rate)
    bar_l, bar_r = [0.0] * bar_n, [0.0] * bar_n
    kick, clap, hat = rng.choice(kit["kick"]), rng.choice(kit["clap"]), rng.choice(kit["hat"])
    hat_gain = 0.12 + 0.1 * rng.random()
    for b in range(4):
        _mix(bar_l, bar_r, round(b * beat_s * rate), kick, 0.6, 0.0)
        if b % 2:
            _mix(bar_l, bar_r, round(b * beat_s * rate), clap, 0.35, 0.1)
        _mix(bar_l, bar_r, round((b + 0.5) * beat_s * rate), hat, hat_gain, -0.2)
    a, end = round(8.0 * rate), round(12.0 * rate)
    while a < end:
        m = min(bar_n, end - a)
        left[a : a + m] = list(map(add, left[a : a + m], bar_l[:m]))
        right[a : a + m] = list(map(add, right[a : a + m], bar_r[:m]))
        a += bar_n
    # 12-20 s: mixed.
    rhythm(12.0, seconds, 0.8)
    t = 12.0
    while t < seconds:
        _mix(left, right, round(t * rate), kick, 0.4, 0.0)
        t += beat_s

    full = 0.5 * 32767.0
    out = array("h", bytes(4 * n))
    out[0::2] = array("h", [int(max(-32767.0, min(32767.0, v * full))) for v in left])
    out[1::2] = array("h", [int(max(-32767.0, min(32767.0, v * full))) for v in right])
    if sys.byteorder == "big":
        out.byteswap()
    return out.tobytes()


# ── SBC (libsbc through ctypes, as in build_libsbc_fixture.py) ───────────────────────────────


def _libsbc_encoder():  # pragma: no cover - needs libsbc on the build machine
    import ctypes

    from tests.fixtures.a2dp_air.build_libsbc_fixture import _Sbc

    lib = ctypes.CDLL("libsbc.so.1")
    lib.sbc_encode.restype = ctypes.c_ssize_t
    lib.sbc_get_codesize.restype = ctypes.c_size_t
    lib.sbc_get_frame_length.restype = ctypes.c_size_t

    class Encoder:
        def __init__(self) -> None:
            self.sbc = _Sbc()
            if lib.sbc_init(ctypes.byref(self.sbc), 0) != 0:
                raise OSError("sbc_init failed")
            # SBC_FREQ_44100, SBC_BLK_16, SBC_SB_8, SBC_MODE_JOINT_STEREO, SBC_AM_LOUDNESS, bitpool 2, SBC_LE
            s = self.sbc
            s.frequency, s.blocks, s.subbands, s.mode, s.allocation, s.bitpool, s.endian = 2, 3, 1, 3, 0, 2, 0
            self.code = lib.sbc_get_codesize(ctypes.byref(s))
            self.out = ctypes.create_string_buffer(lib.sbc_get_frame_length(ctypes.byref(s)) + 16)
            self.written = ctypes.c_ssize_t()

        def frame(self, pcm: bytes) -> bytes:
            used = lib.sbc_encode(
                ctypes.byref(self.sbc), pcm, len(pcm), self.out, len(self.out), ctypes.byref(self.written)
            )
            if used != self.code:
                raise OSError(f"sbc_encode consumed {used} of {self.code} bytes")
            return self.out.raw[: self.written.value]

        def close(self) -> None:
            lib.sbc_finish(ctypes.byref(self.sbc))

    return Encoder


# ── capture ───────────────────────────────────────────────────────────────────────────────────


def _record(t_s: float, opcode: int, payload: bytes) -> bytes:
    ts_us = round((UNIX_T0 + t_s) * 1e6) + _BTSNOOP_EPOCH_DELTA_US
    flags = (INDEX << 16) | opcode
    return struct.pack(">IIIIq", len(payload), len(payload), flags, 0, ts_us) + payload


def _event(code: int, params: bytes) -> bytes:
    return bytes([code, len(params)]) + params


def build(pcm: bytes, seed: int = SEED) -> tuple[bytes, dict]:  # pragma: no cover - needs libsbc
    """``(btsnoop bytes, truth dict)`` for the stereo S16LE ``pcm``."""
    encoder_cls = _libsbc_encoder()
    rng = random.Random(seed ^ 0xA2D9)
    total = len(pcm) // 4
    pkt_s = FRAMES_PER_PACKET * FRAME_SAMPLES / RATE
    events: list[tuple[float, int, int, bytes]] = []  # (t, order, opcode, payload)
    bdaddr = bytes.fromhex("6cfeedc0ffee")
    events.append((0.0, 0, _MON_EVENT, _event(0x03, struct.pack("<BH", 0, HANDLE) + bdaddr + bytes([0x01, 0x00]))))
    frames_truth: list[list[int]] = []
    encoder = encoder_cls()
    pos = 0  # next PCM sample to encode
    seq = ts = 0
    ssrc = SSRCS[0]
    k = 0  # packet slot on the send cadence
    t_offset = 0.0
    skipped = suspended = False
    skip_info: dict = {}
    suspend_info: dict = {}
    order = 1
    try:
        while pos + FRAMES_PER_PACKET * FRAME_SAMPLES <= total:
            t_slot = FIRST_PACKET_S + k * pkt_s + t_offset
            if not skipped and t_slot >= SKIP_AT_S:
                skipped = True
                skip_info = {"pcm_from": pos, "samples": SKIP_SAMPLES, "rtp_ts_step": SKIP_SAMPLES, "packet_seq": seq}
                pos += SKIP_SAMPLES
                ts += SKIP_SAMPLES
                continue
            if not suspended and t_slot >= SUSPEND_AT_S:
                suspended = True
                gap = round(SUSPEND_S * RATE)
                suspend_info = {"pcm_from": pos, "samples": gap, "resume_s": t_slot + SUSPEND_S}
                pos += gap
                t_offset += SUSPEND_S
                encoder.close()
                encoder = encoder_cls()
                seq = ts = 0
                ssrc = SSRCS[1]
                continue
            t_send = t_slot + SEND_LATE_MAX_S * rng.random()
            body = bytearray([FRAMES_PER_PACKET])
            first = pos
            for _ in range(FRAMES_PER_PACKET):
                body += encoder.frame(pcm[4 * pos : 4 * (pos + FRAME_SAMPLES)])
                pos += FRAME_SAMPLES
            rtp = struct.pack(">BBHII", 0x80, 96, seq, ts, ssrc)
            sdu = struct.pack("<HH", len(rtp) + len(body), CID) + rtp + bytes(body)
            pieces = [sdu[i : i + ACL_FRAGMENT] for i in range(0, len(sdu), ACL_FRAGMENT)]
            last_us = 0
            for j, piece in enumerate(pieces):
                pb = 0b10 if j == 0 else 0b01
                t_frag = t_send + j * 1e-4
                events.append((t_frag, order, _MON_ACL_TX, struct.pack("<HH", HANDLE | (pb << 12), len(piece)) + piece))
                order += 1
                delay = rng.uniform(*NOCP_DELAY_S)
                events.append((t_frag + delay, order, _MON_EVENT, _event(0x13, struct.pack("<BHH", 1, HANDLE, 1))))
                order += 1
                last_us = round(t_frag * 1e6)
            for f in range(FRAMES_PER_PACKET):
                frames_truth.append([first + f * FRAME_SAMPLES, last_us, f])
            seq = (seq + 1) & 0xFFFF
            ts = (ts + FRAMES_PER_PACKET * FRAME_SAMPLES) & 0xFFFFFFFF
            k += 1
    finally:
        encoder.close()
    events.sort(key=lambda e: (round(e[0] * 1e6), e[1]))
    out = bytearray(b"btsnoop\0" + struct.pack(">II", 1, 2001))
    for t, _order, opcode, payload in events:
        out += _record(t, opcode, payload)
    truth = {
        "version": 1,
        "rate": RATE,
        "frame_samples": FRAME_SAMPLES,
        "frames_per_packet": FRAMES_PER_PACKET,
        "index": INDEX,
        "handle": HANDLE,
        "unix_t0": UNIX_T0,
        "seed": seed,
        "pcm_samples": total,
        "pcm_sha256": hashlib.sha256(pcm).hexdigest(),
        "skip": skip_info,
        "suspend": suspend_info,
        "sections": [list(s) for s in SECTIONS],
        "frames": frames_truth,  # [first PCM sample, t_send µs after the first record, position in packet]
    }
    return bytes(out), truth


def _gzip(path: Path, data: bytes) -> None:
    with open(path, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, filename="") as gz:
        gz.write(data)


def install(path: Path, data: bytes, expected: str | None) -> str:
    """Gzip ``data`` to ``<path>.new``; move it onto ``path`` only when its sha256 is ``expected``.

    A mismatch leaves the committed file untouched and the candidate at ``<path>.new``."""
    tmp = path.with_name(path.name + ".new")
    _gzip(tmp, data)
    digest = hashlib.sha256(tmp.read_bytes()).hexdigest()
    if digest == expected:
        os.replace(tmp, path)
    return digest


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - needs libsbc
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out-dir", type=Path, default=HERE)
    args = ap.parse_args(argv)
    from tests.fixtures.a2dp_air.build_libsbc_fixture import libsbc_version

    version = libsbc_version()
    if version != LIBSBC_VERSION:
        print(f"warning: libsbc {version} loaded, the fixture was built with {LIBSBC_VERSION}")
    snoop, truth = build(music_pcm())
    outputs = {
        "air_music.btsnoop.gz": snoop,
        "air_music_truth.json.gz": json.dumps(truth, separators=(",", ":"), sort_keys=True).encode(),
    }
    mismatches = []
    for name, data in outputs.items():
        digest = install(args.out_dir / name, data, EXPECTED_SHA256.get(name))
        if digest != EXPECTED_SHA256.get(name):
            mismatches.append(name)
        print(f"{name}: {len(data)} bytes raw, sha256 {digest}")
    if mismatches:
        print(f"error: outputs differ from the recorded sha256 (left as .new): {', '.join(mismatches)}")
        return 1
    return 0


if __name__ == "__main__":
    ROOT = Path(__file__).resolve().parents[3]
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    sys.exit(main())
