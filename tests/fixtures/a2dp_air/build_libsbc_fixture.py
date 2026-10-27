"""Rebuild the libsbc-encoded click-track fixtures under ``libsbc/``.

Encodes 12.5 s of the sync click track (``sendspin_sync.click_track.render_pcm``
at 44.1 kHz, clicks at ``LEAD_IN_S + k*PERIOD_S``) with the system libsbc — the
encoder BlueZ and PulseAudio use — through ctypes, once per sub-band count
(8, 4) and per lead of N silent samples (N = 0, 17, 40, 64, 100, 127, so the
chirps land at different places inside the 128/64-sample frames). With PCM
sample 0 at t = 0 the true click phase is ``N / 44.1`` ms; the phase tool must
recover it from the frames' scale factors.

Parameters: 44.1 kHz, 16 blocks, joint stereo, loudness, bitpool 2. The
bitpool only drives bit allocation; scale factors (all the energy proxy reads)
are computed before it, so a tiny bitpool keeps the files small (~0.6 KB
gzip each) without changing what is measured. Built with libsbc 1.3.1
(``libsbc.so.1``); the gzip mtime is fixed, so rebuilding is reproducible
for the same libsbc.

    uv run python tests/fixtures/a2dp_air/build_libsbc_fixture.py
"""

from __future__ import annotations

import ctypes
import gzip
from pathlib import Path

from sendspin_sync import click_track as ct

HERE = Path(__file__).resolve().parent / "libsbc"
RATE = 44100
DURATION_S = 12.5
LEADS = (0, 17, 40, 64, 100, 127)
SUBBANDS = {8: 0x01, 4: 0x00}  # SBC_SB_8 / SBC_SB_4


class _Sbc(ctypes.Structure):  # struct sbc_struct (sbc/sbc.h)
    _fields_ = [
        ("flags", ctypes.c_ulong),
        ("frequency", ctypes.c_uint8),
        ("blocks", ctypes.c_uint8),
        ("subbands", ctypes.c_uint8),
        ("mode", ctypes.c_uint8),
        ("allocation", ctypes.c_uint8),
        ("bitpool", ctypes.c_uint8),
        ("endian", ctypes.c_uint8),
        ("priv", ctypes.c_void_p),
        ("priv_alloc_base", ctypes.c_void_p),
    ]


def encode(pcm: bytes, subbands_flag: int) -> bytes:
    lib = ctypes.CDLL("libsbc.so.1")
    lib.sbc_encode.restype = ctypes.c_ssize_t
    lib.sbc_get_codesize.restype = ctypes.c_size_t
    lib.sbc_get_frame_length.restype = ctypes.c_size_t
    sbc = _Sbc()
    if lib.sbc_init(ctypes.byref(sbc), 0) != 0:
        raise OSError("sbc_init failed")
    # SBC_FREQ_44100, SBC_BLK_16, SBC_MODE_JOINT_STEREO, SBC_AM_LOUDNESS, bitpool, SBC_LE
    sbc.frequency, sbc.blocks, sbc.subbands, sbc.mode, sbc.allocation, sbc.bitpool, sbc.endian = (
        0x02,
        0x03,
        subbands_flag,
        0x03,
        0x00,
        2,
        0x00,
    )
    try:
        code = lib.sbc_get_codesize(ctypes.byref(sbc))
        out_buf = ctypes.create_string_buffer(lib.sbc_get_frame_length(ctypes.byref(sbc)) + 16)
        written = ctypes.c_ssize_t()
        out = bytearray()
        for i in range(0, len(pcm) - code + 1, code):
            chunk = pcm[i : i + code]
            used = lib.sbc_encode(ctypes.byref(sbc), chunk, len(chunk), out_buf, len(out_buf), ctypes.byref(written))
            if used != code:
                raise OSError(f"sbc_encode consumed {used} of {code} bytes")
            out += out_buf.raw[: written.value]
        return bytes(out)
    finally:
        lib.sbc_finish(ctypes.byref(sbc))


def main() -> None:
    HERE.mkdir(exist_ok=True)
    pcm = ct.render_pcm(duration_s=DURATION_S, sample_rate=RATE)
    for m, flag in SUBBANDS.items():
        for lead in LEADS:
            sbc = encode(bytes(4 * lead) + pcm, flag)
            path = HERE / f"m{m}_lead{lead}.sbc.gz"
            with open(path, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, filename="") as gz:
                gz.write(sbc)
            print(f"{path.name}: {len(sbc)} bytes SBC, {path.stat().st_size} bytes gzip")


if __name__ == "__main__":
    main()
