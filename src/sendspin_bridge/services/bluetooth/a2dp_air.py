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
per connection handle. NOCP counts packets that were *transmitted or flushed*
(Core v5.x Vol 4 Part E §7.7.19), so a completion time is "left the
controller", not "acknowledged by the speaker". Crediting NOCP counts FIFO to
the fragments seen on the monitor gives each fragment its completion time —
but only if the parser's queue holds exactly the fragments the controller
holds. A capture (or a live monitor socket) that starts while the controller
still has K fragments queued is K short, and with a standing backlog (the
controller transmitting as fast as the host sends) the parser's queue empties
on every credit while the real one never does: a naive FIFO then hands every
packet the completion of the packet K fragments earlier. Counts alone cannot
tell K = 0 from K > 0, so alignment is trusted only on evidence that the
controller's queue was empty:

* a *Connection Complete* for the handle inside the capture (nothing can be
  queued on a new connection), or
* no ACL TX on the handle for more than :data:`IDLE_SYNC_S` while the parser's
  queue is empty (the controller has transmitted or flushed everything; the
  credits for pre-capture fragments arrived as "excess" and were discarded).
  The start of the capture counts as the previous send for a handle never seen
  before; a link forgotten to make room keeps its last send time (bounded
  memory, see :data:`MAX_EVICTED_KEYS`; past that, re-created links count from
  their first new send). Leftover fragments in the parser's queue block this on
  purpose: after a lost completion they stay uncredited for good, but they are
  indistinguishable from a controller that stalled with fragments queued, whose
  late credits would land on new ones. The rule assumes the controller
  transmits or flushes within that time; one that stalls longer while holding
  pre-capture fragments the parser never saw defeats it (releasing credited
  times only at the next send, below, catches the stall's late credit only if
  it arrives before that send).

From there the link is *in step*. A credited time is held until the parser's
queue has drained and the link sends again while still in step, so a late
credit for a fragment the parser never saw shows up as "excess" first. If the
queue does not drain within :data:`DRAIN_DEADLINE_S` /
:data:`DRAIN_DEADLINE_EVENTS` NOCP events (a lost completion, or a radio that
fell behind), the held packets are released with ``None`` — the link stays in
step, so crediting resumes as soon as the queue drains again; after a lost
completion it never drains and every packet stays ``None``. The link leaves
step on an excess credit, on a growing monitor drop counter (on every link:
the counter is not attributed to an index), when its queue overflows
:data:`MAX_IN_FLIGHT`, and on a controller reset. Anything not proven stays
``t_complete=None``; a lost TX record that is not reported as a drop is the one
case these rules cannot catch. :meth:`A2dpAirParser.link_states` exposes
per-link counters so a caller can tell a "blind" link from a working one.

Monitor drops: ``btmon -w`` writes 0 in every btsnoop record's drop field, so
offline captures never report drops. A live reader of the monitor socket must
feed the kernel's receive-queue overflow count (``SO_RXQ_OVFL`` ancillary data)
as ``drops``; there is no live reader yet.
"""

from __future__ import annotations

import enum
import gzip
import struct
from collections import OrderedDict, deque
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from sendspin_bridge.services.bluetooth.hci_socket import (
    MONITOR_ACL_TX_PKT,
    MONITOR_COMMAND_PKT,
    MONITOR_DEL_INDEX,
    MONITOR_EVENT_PKT,
    MONITOR_NEW_INDEX,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator
    from pathlib import Path

__all__ = [
    "A2dpAirParser",
    "AclReassembler",
    "ChannelMode",
    "L2capSdu",
    "LinkState",
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
HCI_EVT_CONNECTION_COMPLETE = 0x03
HCI_EVT_HARDWARE_ERROR = 0x10
HCI_OP_RESET = 0x0C03
IDLE_SYNC_S = 0.5  # no sends for this long with nothing outstanding: the controller's queue is empty
DRAIN_DEADLINE_S = 1.0  # while in step the queue must empty at least this often ...
DRAIN_DEADLINE_EVENTS = 64  # ... and within this many NOCP events for the link
MAX_IN_FLIGHT = 256  # fragments outstanding per link before it is cleared
MAX_EVICTED_KEYS = 4096  # last send times remembered for links forgotten to make room
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
    desyncs: int = 0  # times a link left step (excess credit, missed drain deadline)
    monitor_drops: int = 0  # increases of the btsnoop cumulative drop counter
    in_flight_overflows: int = 0  # links cleared because their queue exceeded MAX_IN_FLIGHT
    links_evicted: int = 0  # idle links forgotten to make room for a new one
    controller_resets: int = 0  # index added/removed, HCI_Reset, Hardware Error


# ── monitor records / btsnoop ──────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class MonitorRecord:
    """One HCI monitor frame: unix time, controller index, monitor opcode, payload."""

    t: float
    index: int
    opcode: int
    payload: bytes
    drops: int = 0  # btsnoop cumulative drop counter at this record


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
            _orig_len, incl_len, flags, drops, ts = _BTSNOOP_RECORD.unpack(head)
            if incl_len > _BTSNOOP_MAX_RECORD:
                break
            data = f.read(incl_len)
            if len(data) < incl_len:
                break
            yield MonitorRecord((ts - BTSNOOP_EPOCH_DELTA_US) / 1e6, flags >> 16, flags & 0xFFFF, data, drops)
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


def _crc8_table() -> tuple[int, ...]:
    table = []
    for byte in range(256):
        crc = byte
        for _ in range(8):
            crc = ((crc << 1) ^ _SBC_CRC_POLY) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
        table.append(crc)
    return tuple(table)


_SBC_CRC_TABLE = _crc8_table()


def _sbc_crc8(data: bytes, nbits: int) -> int:
    """SBC CRC-8 over the first *nbits* bits of *data* (whole bytes by table, the tail bit by bit)."""
    crc = _SBC_CRC_INIT
    whole = nbits >> 3
    for byte in data[:whole]:
        crc = _SBC_CRC_TABLE[crc ^ byte]
    for i in range(whole * 8, nbits):
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
    padding: int = 0  # trailing padding bytes (P bit), already validated against the length


def parse_rtp(data: bytes) -> RtpHeader | None:
    """RFC 3550 fixed header (plus CSRC list, header extension and padding), or ``None``."""
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
    padding = 0
    if b0 & 0x20:
        padding = data[-1]
        if padding == 0 or end + padding > len(data):
            return None
    return RtpHeader(b1 & 0x7F, bool(b1 & 0x80), seq, ts, ssrc, end, padding)


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


def _media_packet(
    t: float, index: int, handle: int, sdu: L2capSdu, stats: ParseStats, sbc_streams: set[int]
) -> MediaPacket | None:
    """*sbc_streams* holds the CIDs of this link already seen carrying SBC (for S=0 frame fragments)."""
    if sdu.cid < L2CAP_FIRST_DYNAMIC_CID:
        return None
    rtp = parse_rtp(sdu.payload)
    if rtp is None or rtp.payload_type not in RTP_DYNAMIC_PT:
        return None
    body = sdu.payload[rtp.header_len : len(sdu.payload) - rtp.padding]
    if not body:
        return None
    codec, fragmented = "unknown", False
    frames: tuple[SbcFrame, ...] = ()
    # SBC media payload header: F S L RFA frame-count(4)
    starts_frame = len(body) > SBC_HEADER_LEN and body[1] == SBC_SYNCWORD
    if starts_frame or (body[0] & 0x80 and not body[0] & 0x40 and sdu.cid in sbc_streams):
        codec = "sbc"
        sbc_streams.add(sdu.cid)
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


@dataclass(frozen=True, slots=True)
class LinkState:
    """Completion-crediting state of one ``(index, handle)`` for health reporting."""

    in_step: bool
    in_flight: int
    credited: int  # media packets released with a completion time
    released_none: int  # media packets released without one
    desyncs: int  # times this link left step


@dataclass
class _Link:
    reassembler: AclReassembler
    last_tx: float  # time of the previous ACL TX (the capture start before the first one)
    last_seen: float
    in_flight: deque[_Fragment] = field(default_factory=deque)
    in_step: bool = False
    held: list[MediaPacket] = field(default_factory=list)  # credited, waiting for the queue to drain
    busy_since: float | None = None  # when the queue last went from empty to non-empty
    events_since_drain: int = 0
    drained: bool = False  # queue emptied while in step; held packets go out at the next send
    sbc_streams: set[int] = field(default_factory=set)
    credited: int = 0
    released_none: int = 0
    desyncs: int = 0

    @property
    def idle(self) -> bool:
        return not self.in_flight and not self.held and self.reassembler.buffered == 0


def _is_hci_reset(opcode: int, payload: bytes) -> bool:
    return opcode == MONITOR_COMMAND_PKT and len(payload) >= 2 and int.from_bytes(payload[:2], "little") == HCI_OP_RESET


class A2dpAirParser:
    """Feed HCI monitor records; get back :class:`MediaPacket` as their fate is known.

    A packet is returned by :meth:`feed` once its completion is proven (the
    queue drained after its last fragment was credited) or known to be
    unknowable, so packets of one link come out in send order. :meth:`flush`
    returns the rest with ``t_complete=None``. Only host→controller traffic
    (ACL TX) is credited; see the module docstring for when completions count.
    """

    def __init__(
        self,
        *,
        max_links: int = 64,
        max_sdu: int = L2CAP_MAX_SDU,
        stats: ParseStats | None = None,
    ) -> None:
        self.max_links = max_links
        self.max_sdu = max_sdu
        self.stats = stats if stats is not None else ParseStats()
        self._links: dict[tuple[int, int], _Link] = {}
        self._start: float | None = None
        self._drops = 0
        self._evicted: OrderedDict[tuple[int, int], float] = OrderedDict()
        self._forgot_evicted = False

    def link_states(self) -> dict[tuple[int, int], LinkState]:
        return {
            key: LinkState(link.in_step, len(link.in_flight), link.credited, link.released_none, link.desyncs)
            for key, link in self._links.items()
        }

    def feed(self, t: float, index: int, opcode: int, payload: bytes, drops: int | None = None) -> list[MediaPacket]:
        """One monitor record; *drops* is the btsnoop cumulative drop counter when known."""
        if self._start is None:
            self._start = t
        out: list[MediaPacket] = []
        if drops is not None and drops != self._drops:
            if drops > self._drops:
                self.stats.monitor_drops += 1
                for link in self._links.values():
                    out += self._leave_step(link, count=False)
            self._drops = drops
        if opcode == MONITOR_ACL_TX_PKT:
            out += self._acl_tx(t, index, payload)
        elif opcode == MONITOR_EVENT_PKT:
            out += self._event(t, index, payload)
        elif opcode in (MONITOR_NEW_INDEX, MONITOR_DEL_INDEX) or _is_hci_reset(opcode, payload):
            out += self._reset_index(index)
        return out

    def flush(self) -> list[MediaPacket]:
        out: list[MediaPacket] = []
        for link in self._links.values():
            out += self._release(link, confirmed=False)
            out += self._clear(link)
        return out

    def _emit(self, link: _Link, packets: list[MediaPacket]) -> list[MediaPacket]:
        for p in packets:
            if p.t_complete is None:
                link.released_none += 1
            else:
                link.credited += 1
        return packets

    # -- link bookkeeping ---------------------------------------------------

    def _release(self, link: _Link, *, confirmed: bool) -> list[MediaPacket]:
        held = link.held
        link.held = []
        link.drained = False
        return self._emit(link, held if confirmed else [replace(p, t_complete=None) for p in held])

    def _clear(self, link: _Link, *, reassembly: bool = True) -> list[MediaPacket]:
        out = [f.packet for f in link.in_flight if f.packet is not None]
        link.in_flight.clear()
        link.busy_since = None
        link.events_since_drain = 0
        if reassembly:
            link.reassembler.reset()
        return self._emit(link, out)

    def _leave_step(self, link: _Link, *, count: bool = True) -> list[MediaPacket]:
        if link.in_step and count:
            self.stats.desyncs += 1
            link.desyncs += 1
        link.in_step = False
        for frag in link.in_flight:
            frag.in_step = False
        return self._release(link, confirmed=False)

    def _drop_link(self, key: tuple[int, int]) -> list[MediaPacket]:
        link = self._links.pop(key, None)
        if link is None:
            return []
        return self._release(link, confirmed=False) + self._clear(link)

    def _reset_index(self, index: int) -> list[MediaPacket]:
        self.stats.controller_resets += 1
        out: list[MediaPacket] = []
        for key in [k for k in self._links if k[0] == index]:
            out += self._drop_link(key)
        return out

    def _new_link(self, key: tuple[int, int], t: float) -> _Link | None:
        if len(self._links) >= self.max_links:
            idle = [
                (link.last_seen, k) for k, link in self._links.items() if link.idle and t - link.last_seen > IDLE_SYNC_S
            ]
            if not idle:
                self.stats.links_refused += 1
                return None
            victim = min(idle)[1]
            self._evicted[victim] = self._links.pop(victim).last_tx
            self._evicted.move_to_end(victim)
            if len(self._evicted) > MAX_EVICTED_KEYS:
                self._evicted.popitem(last=False)
                self._forgot_evicted = True
            self.stats.links_evicted += 1
        if key in self._evicted:
            last_tx = self._evicted.pop(key)
        elif self._forgot_evicted:
            last_tx = t  # it may be a forgotten link: only a fresh idle gap counts
        else:
            last_tx = self._start if self._start is not None else t
        link = self._links[key] = _Link(AclReassembler(self.max_sdu, self.stats), last_tx=last_tx, last_seen=t)
        return link

    def _check_deadline(self, link: _Link, t: float) -> list[MediaPacket]:
        """Bound the wait for a drain: release what is held as unknown, but stay in step."""
        if link.busy_since is not None and (
            t - link.busy_since > DRAIN_DEADLINE_S or link.events_since_drain > DRAIN_DEADLINE_EVENTS
        ):
            link.busy_since = t
            link.events_since_drain = 0
            return self._release(link, confirmed=False)
        return []

    # -- records ------------------------------------------------------------

    def _acl_tx(self, t: float, index: int, payload: bytes) -> list[MediaPacket]:
        if len(payload) < ACL_HEADER_LEN:
            self.stats.malformed_records += 1
            return []
        flags, length = struct.unpack_from("<HH", payload, 0)
        handle, pb = flags & 0x0FFF, (flags >> 12) & 0b11
        key = (index, handle)
        link = self._links.get(key) or self._new_link(key, t)
        if link is None:
            return []
        out: list[MediaPacket] = []
        if link.drained:
            out += self._release(link, confirmed=link.in_step)
        if not link.in_flight:
            if not link.in_step and t - link.last_tx > IDLE_SYNC_S:
                link.in_step = True  # idle long enough: the controller's queue is empty
            link.busy_since = t
            link.events_since_drain = 0
        link.last_tx = link.last_seen = t
        if len(link.in_flight) >= MAX_IN_FLIGHT:
            self.stats.in_flight_overflows += 1
            out += self._leave_step(link)
            out += self._clear(link, reassembly=False)
            link.busy_since = t
        out += self._check_deadline(link, t)
        # Every ACL packet costs one completion credit, even one we cannot parse.
        frag = _Fragment(link.in_step)
        link.in_flight.append(frag)
        data = payload[ACL_HEADER_LEN : ACL_HEADER_LEN + length]
        if len(data) < length:
            self.stats.malformed_records += 1
            link.reassembler.reset()
            return out
        sdu = link.reassembler.push(pb, data)
        if sdu is not None:
            frag.packet = _media_packet(t, index, handle, sdu, self.stats, link.sbc_streams)
        return out

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
        if code == HCI_EVT_CONNECTION_COMPLETE and len(params) >= 10 and params[0] == 0 and params[9] == 0x01:
            (handle,) = struct.unpack_from("<H", params, 1)
            key = (index, handle & 0x0FFF)
            out = self._drop_link(key)
            link = self._new_link(key, t)
            if link is not None:
                link.in_step = True  # nothing can be queued on a new connection
            return out
        if code == HCI_EVT_DISCONNECT_COMPLETE and len(params) >= 3 and params[0] == 0:
            (handle,) = struct.unpack_from("<H", params, 1)
            return self._drop_link((index, handle & 0x0FFF))
        if code == HCI_EVT_HARDWARE_ERROR:
            return self._reset_index(index)
        return []

    def _credit(self, link: _Link, t: float, count: int) -> list[MediaPacket]:
        out: list[MediaPacket] = []
        link.last_seen = t
        link.events_since_drain += 1
        credited = min(count, len(link.in_flight))
        for _ in range(credited):
            frag = link.in_flight.popleft()
            if frag.packet is None:
                continue
            if frag.in_step and link.in_step:
                link.held.append(replace(frag.packet, t_complete=t))
            else:
                out += self._emit(link, [frag.packet])
        if count > credited:
            self.stats.excess_completions += count - credited
            out += self._leave_step(link)
        if not link.in_flight:
            link.drained = link.in_step and bool(link.held)
            link.busy_since = None
            link.events_since_drain = 0
        else:
            out += self._check_deadline(link, t)
        if len(link.held) > MAX_IN_FLIGHT:
            out += self._release(link, confirmed=False)
        return out


def parse_records(records: Iterable[MonitorRecord], parser: A2dpAirParser | None = None) -> list[MediaPacket]:
    """Run *records* through a parser (a fresh one by default) and return every media packet in emit order."""
    p = parser if parser is not None else A2dpAirParser()
    out: list[MediaPacket] = []
    for rec in records:
        out += p.feed(rec.t, rec.index, rec.opcode, rec.payload, rec.drops)
    return out + p.flush()
