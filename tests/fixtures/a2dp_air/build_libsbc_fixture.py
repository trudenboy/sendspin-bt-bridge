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
gzip each) without changing what is measured. Built with libsbc
``LIBSBC_VERSION``; the gzip mtime is fixed, so a rebuild with that libsbc must
reproduce ``EXPECTED_SHA256`` exactly. The script warns when the loaded
libsbc is another version and exits 1 when any output differs.

    uv run python tests/fixtures/a2dp_air/build_libsbc_fixture.py
"""

from __future__ import annotations

import ctypes
import gzip
import hashlib
import os
import sys
from pathlib import Path

from sendspin_sync import click_track as ct

HERE = Path(__file__).resolve().parent / "libsbc"
RATE = 44100
DURATION_S = 12.5
LEADS = (0, 17, 40, 64, 100, 127)
SUBBANDS = {8: 0x01, 4: 0x00}  # SBC_SB_8 / SBC_SB_4
LIBSBC_VERSION = "1.3.1"
EXPECTED_SHA256 = {
    "m4_lead0.sbc.gz": "db39b1570a95860819ead523cc76aa3aa8f3c91a6a2c9aa2e4aeda9d6072fe9f",
    "m4_lead100.sbc.gz": "2199cde7b31dd7cb4491ac379942238011690825c3c826369abd6f31c15371eb",
    "m4_lead127.sbc.gz": "c9536d432745ae29eced583f07d6b82506711e21ba89c63ff2b045d2cb0696fe",
    "m4_lead17.sbc.gz": "5e9965c3275db53caaebede9b8944683fd0076a7b2c3f375ca7affca6caae396",
    "m4_lead40.sbc.gz": "ae0acab0462d0adda92aa2085ded9d2474c95124f5d6dcb6e6bd669f0d7d1fb4",
    "m4_lead64.sbc.gz": "e60de0660bc82e920f913e784b14b790ecf2c2c26dd12e46aaaf14fbae1a6ab3",
    "m8_lead0.sbc.gz": "dc22797b148bf49b1ec337c8ed1e19731b2b02a6954183892188b9b89510527a",
    "m8_lead100.sbc.gz": "140b3f11ac3f60fbf0fb1bcb270120ee278968308c36d296b437c487f8c54e73",
    "m8_lead127.sbc.gz": "c55dee35fc7bf16a846e459ba9a0e1d3b8cb7e68a10122f0fe0b5608180d0b4e",
    "m8_lead17.sbc.gz": "812e19e82370ff8fd8cb844fa43973121ab0679a6c3bc621502d037fba11c3d1",
    "m8_lead40.sbc.gz": "78ae0ae8dc93745691f57beaa4e3c0a7dfcb2491922d774ecef11271ecce39b7",
    "m8_lead64.sbc.gz": "bc1d351785da3812df3b103182ddf6aa6ea708226d16a924f942792f1ee522f2",
}


def libsbc_version() -> str | None:
    """Version of the loaded ``libsbc.so.1``, read from the real file name it maps to."""
    ctypes.CDLL("libsbc.so.1")
    with open("/proc/self/maps") as maps:
        for line in maps:
            path = line.split()[-1]
            if "libsbc.so" in path:
                name = os.path.basename(os.path.realpath(path))
                return name.split(".so.", 1)[1] if ".so." in name else None
    return None


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


def main() -> int:
    version = libsbc_version()
    if version != LIBSBC_VERSION:
        print(f"warning: libsbc {version} loaded, fixtures were built with {LIBSBC_VERSION}")
    HERE.mkdir(exist_ok=True)
    pcm = ct.render_pcm(duration_s=DURATION_S, sample_rate=RATE)
    mismatches = []
    for m, flag in SUBBANDS.items():
        for lead in LEADS:
            sbc = encode(bytes(4 * lead) + pcm, flag)
            path = HERE / f"m{m}_lead{lead}.sbc.gz"
            with open(path, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, filename="") as gz:
                gz.write(sbc)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != EXPECTED_SHA256.get(path.name):
                mismatches.append(path.name)
            print(f"{path.name}: {len(sbc)} bytes SBC, {path.stat().st_size} bytes gzip, sha256 {digest}")
    if mismatches:
        print(f"error: outputs differ from the recorded sha256: {', '.join(mismatches)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
