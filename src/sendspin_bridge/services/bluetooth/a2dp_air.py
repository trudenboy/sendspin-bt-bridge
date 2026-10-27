"""A2DP media at the air interface, decoded from HCI monitor traffic.

The audio a speaker plays has left the host at the moment its ACL fragments
were handed to the controller and, more precisely, at the moment the
controller reports them sent with *Number Of Completed Packets*. Both moments
are visible on ``HCI_CHANNEL_MONITOR`` (what ``btmon`` reads). This module turns
that traffic into :class:`MediaPacket` records — one per A2DP media packet with
its RTP sequence/timestamp, its decoded SBC frame headers (including the
per-sub-band scale factors, a cheap energy proxy), the send time of its last
ACL fragment and the completion time of that fragment.

Layers, each usable on its own:

* :func:`read_btsnoop` — monitor-datalink (2001) btsnoop files → :class:`MonitorRecord`;
* :class:`AclReassembler` — ACL fragments of one link → L2CAP SDUs;
* :func:`parse_rtp`, :func:`parse_sbc_frame`, :func:`parse_sbc_frames` — media payloads;
* :class:`A2dpAirParser` — ties it together per ``(index, handle)`` and credits completions.

Everything here is pure parsing with no I/O except :func:`read_btsnoop`. Bad
bytes never raise: they are skipped and counted in :class:`ParseStats`.

Completion crediting
--------------------
BR/EDR ACL flow control is per packet: every ACL fragment the host sends is
later reported once in a *Number Of Completed Packets* (NOCP) event, in order
per connection handle. Crediting NOCP counts FIFO to the fragments seen on the
monitor therefore gives each fragment its completion time — provided the
monitor saw every fragment still in the controller. A capture (or a live
monitor socket) that starts mid-stream misses the fragments already queued, so
the first NOCP credits belong to packets that are not in the capture and a
naive FIFO would misalign every later packet by that unknown count.

The tracker handles this conservatively:

* A credit that arrives while no captured fragment is outstanding belongs to a
  pre-capture fragment and is discarded ("excess"). This makes the queue
  converge to the truth at the first moment the controller really drains.
* Until then the alignment is unknown, so fragments sent before the link is
  *in step* are credited with ``None``. The link counts as in step after
  ``sync_drains`` consecutive NOCP events that left the queue empty without any
  excess credit. A2DP drains between packets on a healthy link, so this costs
  a few packets at the start of a capture; a link whose queue never drains
  stays out of step and reports ``None`` (unknown) rather than a wrong time.
* An excess credit after the link was in step (a fragment the monitor missed)
  is counted in ``ParseStats.desyncs`` and drops the link out of step again.
"""

from __future__ import annotations

import enum
import gzip
import struct
from collections import deque
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from sendspin_bridge.services.bluetooth.hci_socket import MONITOR_ACL_TX_PKT, MONITOR_EVENT_PKT

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator
    from pathlib import Path

__all__ = [
    "A2dpAirParser",
    "AclReassembler",
    "ChannelMode",
    "L2capSdu",
    "MediaPacket",
    "MonitorRecord",
    "ParseStats",
    "RtpHeader",
    "SbcFrame",
    "parse_records",
    "parse_rtp",
    "parse_sbc_frame",
    "parse_sbc_frames",
    "read_btsnoop",
    "sbc_frame_length",
]

# ── constants ──────────────────────────────────────────────────────────────

HCI_EVT_DISCONNECT_COMPLETE = 0x05
HCI_EVT_NUMBER_OF_COMPLETED_PACKETS = 0x13
ACL_HEADER_LEN = 4
ACL_PB_CONTINUATION = 0b01
L2CAP_HEADER_LEN = 4
L2CAP_FIRST_DYNAMIC_CID = 0x0040
L2CAP_MAX_SDU = 0xFFFF
RTP_VERSION = 2
RTP_HEADER_LEN = 12
RTP_DYNAMIC_PT = range(96, 128)
SBC_SYNCWORD = 0x9C
SBC_HEADER_LEN = 4
SBC_SAMPLING_RATES = (16000, 32000, 44100, 48000)
SBC_BLOCKS = (4, 8, 12, 16)
_SBC_CRC_POLY = 0x1D  # x^8 + x^4 + x^3 + x^2 + 1
_SBC_CRC_INIT = 0x0F
BTSNOOP_MAGIC = b"btsnoop\0"
BTSNOOP_DATALINK_MONITOR = 2001
BTSNOOP_EPOCH_DELTA_US = 0x00DCDDB30F2F8000  # microseconds from 0000-01-01 to 1970-01-01
_BTSNOOP_RECORD = struct.Struct(">IIIIq")
_BTSNOOP_MAX_RECORD = 0x10000 + 64  # a monitor frame never exceeds 64 KiB; larger means garbage


# ── stats ──────────────────────────────────────────────────────────────────


@dataclass
class ParseStats:
    """Counters for everything that was skipped instead of raising."""

    malformed_records: int = 0  # monitor records too short / inconsistent to parse
    truncated_records: int = 0  # btsnoop record cut off at the end of the file
    links_refused: int = 0  # records for a new (index, handle) beyond ``max_links``
    dropped_continuations: int = 0  # continuation fragment with no SDU in progress
    dropped_incomplete: int = 0  # SDU abandoned because a new start fragment arrived
    dropped_malformed: int = 0  # SDU longer than declared or above the size bound
    sbc_errors: int = 0  # SBC payload whose frames stopped early (bad header / CRC / truncated)
    excess_completions: int = 0  # NOCP credits with no captured fragment outstanding
    desyncs: int = 0  # excess credits after the link was in step


# ── monitor records / btsnoop ──────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class MonitorRecord:
    """One HCI monitor frame: unix time, controller index, monitor opcode, payload."""

    t: float
    index: int
    opcode: int
    payload: bytes


def read_btsnoop(path: str | Path, stats: ParseStats | None = None) -> Iterator[MonitorRecord]:
    """Yield the records of a ``btmon -w`` capture (btsnoop, monitor datalink 2001).

    Gzip-compressed files are read transparently. Raises ``ValueError`` when the
    file is not a monitor btsnoop; a truncated or garbled tail ends the iteration
    (counted in ``stats.truncated_records``).
    """
    with open(path, "rb") as raw:
        gz = raw.read(2) == b"\x1f\x8b"
    opener = gzip.open if gz else open
    with opener(path, "rb") as f:
        header = f.read(16)
        if len(header) < 16 or header[:8] != BTSNOOP_MAGIC:
            raise ValueError(f"{path}: not a btsnoop file")
        _version, datalink = struct.unpack(">II", header[8:])
        if datalink != BTSNOOP_DATALINK_MONITOR:
            raise ValueError(f"{path}: btsnoop datalink {datalink}, need {BTSNOOP_DATALINK_MONITOR} (btmon -w)")
        while True:
            head = f.read(_BTSNOOP_RECORD.size)
            if not head:
                return
            if len(head) < _BTSNOOP_RECORD.size:
                break
            _orig_len, incl_len, flags, _drops, ts = _BTSNOOP_RECORD.unpack(head)
            if incl_len > _BTSNOOP_MAX_RECORD:
                break
            data = f.read(incl_len)
            if len(data) < incl_len:
                break
            yield MonitorRecord((ts - BTSNOOP_EPOCH_DELTA_US) / 1e6, flags >> 16, flags & 0xFFFF, data)
    if stats is not None:
        stats.truncated_records += 1


# ── SBC ────────────────────────────────────────────────────────────────────


class ChannelMode(enum.IntEnum):
    MONO = 0
    DUAL_CHANNEL = 1
    STEREO = 2
    JOINT_STEREO = 3


@dataclass(frozen=True, slots=True)
class SbcFrame:
    """Header fields and scale factors of one SBC frame (A2DP v1.3 §12.6)."""

    sampling_rate: int
    blocks: int
    subbands: int
    channel_mode: ChannelMode
    allocation_snr: bool  # False = loudness
    bitpool: int
    length: int  # bytes, header included
    join: tuple[bool, ...]  # per sub-band, joint stereo only (the last bit is RFA)
    scale_factors: tuple[tuple[int, ...], ...]  # [channel][sub-band], 0..15 (log2 of the sub-band's peak)

    @property
    def channels(self) -> int:
        return 1 if self.channel_mode is ChannelMode.MONO else 2

    @property
    def samples(self) -> int:
        """PCM samples per channel the frame decodes to."""
        return self.blocks * self.subbands


def sbc_frame_length(mode: ChannelMode, subbands: int, blocks: int, bitpool: int) -> int:
    """Frame length in bytes (A2DP v1.3 §12.9)."""
    channels = 1 if mode is ChannelMode.MONO else 2
    base = SBC_HEADER_LEN + (4 * subbands * channels) // 8
    if mode in (ChannelMode.MONO, ChannelMode.DUAL_CHANNEL):
        bits = blocks * channels * bitpool
    elif mode is ChannelMode.STEREO:
        bits = blocks * bitpool
    else:
        bits = subbands + blocks * bitpool
    return base + (bits + 7) // 8


def _sbc_crc8(data: bytes, nbits: int) -> int:
    crc = _SBC_CRC_INIT
    for i in range(nbits):
        bit = (data[i >> 3] >> (7 - (i & 7))) & 1
        top = crc >> 7
        crc = (crc << 1) & 0xFF
        if top ^ bit:
            crc ^= _SBC_CRC_POLY
    return crc


def parse_sbc_frame(data: bytes, offset: int = 0, *, check_crc: bool = True) -> SbcFrame | None:
    """Decode the SBC frame starting at ``data[offset]``; ``None`` if it is not a whole valid frame."""
    if offset < 0 or len(data) - offset < SBC_HEADER_LEN or data[offset] != SBC_SYNCWORD:
        return None
    b1, bitpool, crc = data[offset + 1], data[offset + 2], data[offset + 3]
    rate = SBC_SAMPLING_RATES[b1 >> 6]
    blocks = SBC_BLOCKS[(b1 >> 4) & 3]
    mode = ChannelMode((b1 >> 2) & 3)
    snr = bool(b1 & 0x02)
    subbands = 8 if b1 & 0x01 else 4
    channels = 1 if mode is ChannelMode.MONO else 2
    max_bitpool = (16 if mode in (ChannelMode.MONO, ChannelMode.DUAL_CHANNEL) else 32) * subbands
    if not 2 <= bitpool <= max_bitpool:
        return None
    length = sbc_frame_length(mode, subbands, blocks, bitpool)
    if len(data) - offset < length:
        return None
    body = data[offset + SBC_HEADER_LEN : offset + length]
    join_bits = subbands if mode is ChannelMode.JOINT_STEREO else 0
    side_bits = join_bits + 4 * subbands * channels
    if check_crc:
        covered = bytes(data[offset + 1 : offset + 3]) + bytes(body[: (side_bits + 7) // 8])
        if _sbc_crc8(covered, 16 + side_bits) != crc:
            return None
    bits = int.from_bytes(body[: (side_bits + 7) // 8], "big")
    total = ((side_bits + 7) // 8) * 8

    def field_at(pos: int, width: int) -> int:
        return (bits >> (total - pos - width)) & ((1 << width) - 1)

    join = tuple(bool(field_at(i, 1)) for i in range(join_bits))
    scale = tuple(
        tuple(field_at(join_bits + 4 * (ch * subbands + sb), 4) for sb in range(subbands)) for ch in range(channels)
    )
    return SbcFrame(rate, blocks, subbands, mode, snr, bitpool, length, join, scale)


def parse_sbc_frames(payload: bytes, *, check_crc: bool = True) -> tuple[tuple[SbcFrame, ...], bool]:
    """Walk consecutive SBC frames; returns ``(frames, complete)``.

    ``complete`` is False when bytes remain that do not form a valid frame.
    """
    frames: list[SbcFrame] = []
    pos = 0
    while pos < len(payload):
        frame = parse_sbc_frame(payload, pos, check_crc=check_crc)
        if frame is None:
            return tuple(frames), False
        frames.append(frame)
        pos += frame.length
    return tuple(frames), True


# ── RTP ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class RtpHeader:
    payload_type: int
    marker: bool
    seq: int
    timestamp: int
    ssrc: int
    header_len: int  # fixed header + CSRC list + extension


def parse_rtp(data: bytes) -> RtpHeader | None:
    """RFC 3550 fixed header (plus CSRC list and header extension), or ``None``."""
    if len(data) < RTP_HEADER_LEN or data[0] >> 6 != RTP_VERSION:
        return None
    b0, b1, seq, ts, ssrc = struct.unpack_from(">BBHII", data, 0)
    end = RTP_HEADER_LEN + 4 * (b0 & 0x0F)
    if b0 & 0x10:
        if len(data) < end + 4:
            return None
        (words,) = struct.unpack_from(">H", data, end + 2)
        end += 4 + 4 * words
    if len(data) < end:
        return None
    return RtpHeader(b1 & 0x7F, bool(b1 & 0x80), seq, ts, ssrc, end)


# ── ACL reassembly ─────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class L2capSdu:
    cid: int
    payload: bytes


class AclReassembler:
    """ACL fragments of one ``(index, handle)`` → L2CAP basic-mode SDUs.

    A start fragment (PB ``00``/``10``/``11``) opens an SDU, continuations
    (PB ``01``) extend it until the L2CAP length is reached. A continuation
    with nothing open, an SDU abandoned by a new start, and an SDU that
    overruns its declared length or ``max_sdu`` are dropped and counted. At most
    ``max_sdu + 4`` bytes are ever buffered.
    """

    def __init__(self, max_sdu: int = L2CAP_MAX_SDU, stats: ParseStats | None = None) -> None:
        self.max_sdu = max_sdu
        self.stats = stats if stats is not None else ParseStats()
        self._buf: bytearray | None = None

    @property
    def buffered(self) -> int:
        return len(self._buf) if self._buf is not None else 0

    @property
    def dropped_continuations(self) -> int:
        return self.stats.dropped_continuations

    @property
    def dropped_incomplete(self) -> int:
        return self.stats.dropped_incomplete

    @property
    def dropped_malformed(self) -> int:
        return self.stats.dropped_malformed

    def reset(self) -> None:
        self._buf = None

    def push(self, pb: int, data: bytes) -> L2capSdu | None:
        if pb == ACL_PB_CONTINUATION:
            if self._buf is None:
                self.stats.dropped_continuations += 1
                return None
            self._buf += data
        else:
            if self._buf is not None:
                self.stats.dropped_incomplete += 1
            self._buf = bytearray(data)
        return self._take()

    def _take(self) -> L2capSdu | None:
        buf = self._buf
        if buf is None or len(buf) < L2CAP_HEADER_LEN:
            return None
        length, cid = struct.unpack_from("<HH", buf, 0)
        need = L2CAP_HEADER_LEN + length
        if length > self.max_sdu or len(buf) > need:
            self.stats.dropped_malformed += 1
            self._buf = None
            return None
        if len(buf) < need:
            return None
        self._buf = None
        return L2capSdu(cid, bytes(buf[L2CAP_HEADER_LEN:]))


# ── media packets ──────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class MediaPacket:
    """One A2DP media packet sent to a speaker.

    ``t_send`` is the monitor time of its last ACL fragment going to the
    controller; ``t_complete`` the time of the NOCP event that reported that
    fragment sent, or ``None`` when unknown (see the module docstring).
    ``codec`` is ``"sbc"`` when the payload is an SBC media payload, otherwise
    ``"unknown"`` (AAC, LDAC, …) with no ``frames``.
    """

    t_send: float
    t_complete: float | None
    index: int
    handle: int
    cid: int
    seq: int
    rtp_ts: int
    ssrc: int
    payload_type: int
    codec: str
    frames: tuple[SbcFrame, ...]
    sbc_fragmented: bool = False  # one SBC frame split over several packets (frames not decoded)


def _media_packet(t: float, index: int, handle: int, sdu: L2capSdu, stats: ParseStats) -> MediaPacket | None:
    if sdu.cid < L2CAP_FIRST_DYNAMIC_CID:
        return None
    rtp = parse_rtp(sdu.payload)
    if rtp is None or rtp.payload_type not in RTP_DYNAMIC_PT or len(sdu.payload) <= rtp.header_len:
        return None
    body = sdu.payload[rtp.header_len :]
    codec, fragmented = "unknown", False
    frames: tuple[SbcFrame, ...] = ()
    if len(body) > SBC_HEADER_LEN and body[1] == SBC_SYNCWORD:
        # SBC media payload header: F S L RFA frame-count(4)
        codec = "sbc"
        fragmented = bool(body[0] & 0x80)
        if not fragmented:
            frames, complete = parse_sbc_frames(body[1:])
            if not complete or len(frames) != body[0] & 0x0F:
                stats.sbc_errors += 1
    return MediaPacket(
        t_send=t,
        t_complete=None,
        index=index,
        handle=handle,
        cid=sdu.cid,
        seq=rtp.seq,
        rtp_ts=rtp.timestamp,
        ssrc=rtp.ssrc,
        payload_type=rtp.payload_type,
        codec=codec,
        frames=frames,
        sbc_fragmented=fragmented,
    )


@dataclass(slots=True)
class _Fragment:
    in_step: bool  # sent while the link's completions were known to be aligned
    packet: MediaPacket | None = None  # set on the last fragment of a media packet


@dataclass
class _Link:
    reassembler: AclReassembler
    in_flight: deque[_Fragment] = field(default_factory=deque)
    in_step: bool = False
    drains: int = 0


class A2dpAirParser:
    """Feed HCI monitor records; get back :class:`MediaPacket` as their fate is known.

    A packet is returned by :meth:`feed` once the NOCP credit for its last
    fragment arrived (or the link disconnected), so packets of one link come
    out in send order. :meth:`flush` returns the rest with ``t_complete=None``.
    Only host→controller traffic (ACL TX) is considered.
    """

    def __init__(
        self,
        *,
        max_links: int = 64,
        max_sdu: int = L2CAP_MAX_SDU,
        sync_drains: int = 2,
        stats: ParseStats | None = None,
    ) -> None:
        self.max_links = max_links
        self.max_sdu = max_sdu
        self.sync_drains = sync_drains
        self.stats = stats if stats is not None else ParseStats()
        self._links: dict[tuple[int, int], _Link] = {}

    def feed(self, t: float, index: int, opcode: int, payload: bytes) -> list[MediaPacket]:
        if opcode == MONITOR_ACL_TX_PKT:
            self._acl_tx(t, index, payload)
            return []
        if opcode == MONITOR_EVENT_PKT:
            return self._event(t, index, payload)
        return []

    def flush(self) -> list[MediaPacket]:
        out: list[MediaPacket] = []
        for link in self._links.values():
            out += [f.packet for f in link.in_flight if f.packet is not None]
            link.in_flight.clear()
        return out

    def _acl_tx(self, t: float, index: int, payload: bytes) -> None:
        if len(payload) < ACL_HEADER_LEN:
            self.stats.malformed_records += 1
            return
        flags, length = struct.unpack_from("<HH", payload, 0)
        handle, pb = flags & 0x0FFF, (flags >> 12) & 0b11
        key = (index, handle)
        link = self._links.get(key)
        if link is None:
            if len(self._links) >= self.max_links:
                self.stats.links_refused += 1
                return
            link = self._links[key] = _Link(AclReassembler(self.max_sdu, self.stats))
        # Every ACL packet costs one completion credit, even one we cannot parse.
        frag = _Fragment(link.in_step)
        link.in_flight.append(frag)
        data = payload[ACL_HEADER_LEN : ACL_HEADER_LEN + length]
        if len(data) < length:
            self.stats.malformed_records += 1
            link.reassembler.reset()
            return
        sdu = link.reassembler.push(pb, data)
        if sdu is not None:
            frag.packet = _media_packet(t, index, handle, sdu, self.stats)

    def _event(self, t: float, index: int, payload: bytes) -> list[MediaPacket]:
        if len(payload) < 2 or len(payload) < 2 + payload[1]:
            self.stats.malformed_records += 1
            return []
        code, params = payload[0], payload[2 : 2 + payload[1]]
        if code == HCI_EVT_NUMBER_OF_COMPLETED_PACKETS:
            if not params or len(params) < 1 + 4 * params[0]:
                self.stats.malformed_records += 1
                return []
            out: list[MediaPacket] = []
            for k in range(params[0]):
                handle, count = struct.unpack_from("<HH", params, 1 + 4 * k)
                link = self._links.get((index, handle & 0x0FFF))
                if link is not None:
                    out += self._credit(link, t, count)
            return out
        if code == HCI_EVT_DISCONNECT_COMPLETE and len(params) >= 3 and params[0] == 0:
            (handle,) = struct.unpack_from("<H", params, 1)
            link = self._links.pop((index, handle & 0x0FFF), None)
            if link is not None:
                return [f.packet for f in link.in_flight if f.packet is not None]
        return []

    def _credit(self, link: _Link, t: float, count: int) -> list[MediaPacket]:
        out: list[MediaPacket] = []
        excess = False
        for _ in range(count):
            if not link.in_flight:
                excess = True
                self.stats.excess_completions += 1
                continue
            frag = link.in_flight.popleft()
            if frag.packet is not None:
                out.append(replace(frag.packet, t_complete=t) if frag.in_step else frag.packet)
        if excess:
            if link.in_step:
                self.stats.desyncs += 1
            link.in_step = False
            link.drains = 0
        elif not link.in_flight:
            link.drains += 1
            if link.drains >= self.sync_drains:
                link.in_step = True
        return out


def parse_records(records: Iterable[MonitorRecord], parser: A2dpAirParser | None = None) -> list[MediaPacket]:
    """Run *records* through a parser (a fresh one by default) and return every media packet in emit order."""
    p = parser if parser is not None else A2dpAirParser()
    out: list[MediaPacket] = []
    for rec in records:
        out += p.feed(rec.t, rec.index, rec.opcode, rec.payload)
    return out + p.flush()
