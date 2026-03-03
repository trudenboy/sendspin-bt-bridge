"""In-process SendspinDaemon subclass that exposes status callbacks for the bridge.

Replaces the subprocess + stdout-parsing approach: BridgeDaemon subclasses
SendspinDaemon and overrides key methods to update the bridge status dict
directly via typed callbacks instead of fragile log-line parsing.
"""

from __future__ import annotations

import logging
import subprocess
from datetime import datetime
from typing import TYPE_CHECKING, Callable

from aiosendspin.models.core import GroupUpdateServerPayload, ServerCommandPayload, ServerStatePayload
from aiosendspin.models.types import PlayerCommand, UndefinedField
from sendspin.daemon.daemon import DaemonArgs, SendspinDaemon

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class BridgeDaemon(SendspinDaemon):
    """SendspinDaemon subclass that mirrors status into the bridge status dict.

    Args:
        args: Standard DaemonArgs for SendspinDaemon.
        status: Shared status dict owned by SendspinClient (updated in-place).
        bluetooth_sink_name: PulseAudio/PipeWire sink name for volume sync.
        on_volume_save: Optional callback(volume_int) called after volume changes
                        to persist the value to config.
    """

    def __init__(
        self,
        args: DaemonArgs,
        status: dict,
        bluetooth_sink_name: str | None,
        on_volume_save: Callable[[int], None] | None = None,
    ) -> None:
        super().__init__(args)
        self._bridge_status = status
        self._bluetooth_sink_name = bluetooth_sink_name
        self._on_volume_save = on_volume_save

    # ── Client creation ──────────────────────────────────────────────────────

    def _create_client(self, static_delay_ms: float = 0.0):
        """Create client and register all bridge listeners."""
        client = super()._create_client(static_delay_ms)
        client.add_group_update_listener(self._on_group_update)
        client.add_metadata_listener(self._on_metadata_update)
        client.add_disconnect_listener(self._on_server_disconnect)
        return client

    # ── Connection lifecycle ─────────────────────────────────────────────────

    async def _handle_server_connection(self, ws) -> None:
        """Mark server connected before passing to parent handler (mDNS mode)."""
        if not self._bridge_status.get('server_connected'):
            self._bridge_status['server_connected_at'] = datetime.now().isoformat()
        self._bridge_status['server_connected'] = True
        self._bridge_status['connected'] = True
        await super()._handle_server_connection(ws)

    def _on_server_disconnect(self) -> None:
        """Clear connection + group state on disconnect."""
        self._bridge_status['server_connected'] = False
        self._bridge_status['connected'] = False
        self._bridge_status['group_name'] = None
        self._bridge_status['group_id'] = None

    # ── Audio / stream events ────────────────────────────────────────────────

    def _handle_format_change(
        self, codec: str | None, sample_rate: int, bit_depth: int, channels: int
    ) -> None:
        super()._handle_format_change(codec, sample_rate, bit_depth, channels)
        self._bridge_status['audio_format'] = (
            f"{codec or 'PCM'} {sample_rate}Hz/{bit_depth}-bit/{channels}ch"
        )

    def _on_stream_event(self, event: str) -> None:
        super()._on_stream_event(event)
        is_playing = event == 'start'
        if self._bridge_status.get('playing') != is_playing:
            self._bridge_status['playing'] = is_playing
            self._bridge_status['state_changed_at'] = datetime.now().isoformat()

    # ── Server commands (volume / mute) ──────────────────────────────────────

    def _handle_server_command(self, payload: ServerCommandPayload) -> None:
        super()._handle_server_command(payload)
        if payload.player is None:
            return
        cmd = payload.player
        if cmd.command == PlayerCommand.VOLUME and cmd.volume is not None:
            self._bridge_status['volume'] = cmd.volume
            self._sync_bt_sink_volume(cmd.volume)
        elif cmd.command == PlayerCommand.MUTE and cmd.mute is not None:
            self._bridge_status['muted'] = cmd.mute

    def _sync_bt_sink_volume(self, volume: int) -> None:
        """Apply volume to the specific Bluetooth sink via pactl."""
        if not self._bluetooth_sink_name:
            return
        try:
            result = subprocess.run(
                ['pactl', 'set-sink-volume', self._bluetooth_sink_name, f'{volume}%'],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if result.returncode == 0:
                logger.info("✓ Synced Bluetooth speaker volume to %d%%", volume)
            if self._on_volume_save:
                self._on_volume_save(volume)
        except Exception as exc:
            logger.debug("Could not sync volume: %s", exc)

    # ── MA group updates ─────────────────────────────────────────────────────

    def _on_group_update(self, payload: GroupUpdateServerPayload) -> None:
        self._bridge_status['group_name'] = payload.group_name or None
        self._bridge_status['group_id'] = payload.group_id
        logger.debug("Group update: id=%s name=%s state=%s", payload.group_id, payload.group_name, payload.playback_state)

    # ── Track metadata ───────────────────────────────────────────────────────

    def _on_metadata_update(self, payload: ServerStatePayload) -> None:
        """Callback receives ServerStatePayload; track info is in payload.metadata."""
        metadata = getattr(payload, 'metadata', None)
        if metadata is None:
            return
        if not isinstance(metadata.title, UndefinedField):
            self._bridge_status['current_track'] = metadata.title
        if not isinstance(metadata.artist, UndefinedField):
            self._bridge_status['current_artist'] = metadata.artist


def _get_sink_description(sink_name: str) -> str | None:
    """Return the PulseAudio sink description (friendly name) for a sink by its PA name.

    sounddevice/PortAudio exposes PA sinks by their *description*, not by their PA name.
    We need to map e.g. 'bluez_sink.FC_58_FA_EB_08_6C.a2dp_sink' → 'ENEBY20'.
    """
    try:
        result = subprocess.run(
            ['pactl', 'list', 'sinks'],
            capture_output=True, text=True, timeout=5,
        )
        description: str | None = None
        in_target = False
        for line in result.stdout.splitlines():
            stripped = line.strip()
            if stripped.startswith('Name:') and sink_name in stripped:
                in_target = True
                description = None
                continue
            if in_target:
                if stripped.startswith('Description:'):
                    description = stripped.split(':', 1)[1].strip()
                    break
                # Hit the next sink block — stop
                if stripped.startswith('Name:'):
                    break
        return description
    except Exception as exc:
        logger.debug("Could not get sink description for %s: %s", sink_name, exc)
        return None


def resolve_audio_device_for_sink(sink_name: str | None):
    """Find the sounddevice AudioDevice matching a PulseAudio/PipeWire sink name.

    PulseAudio/PipeWire exposes sinks to sounddevice/PortAudio by their *description*
    (friendly name), not by their PA sink identifier.  We therefore:
      1. Ask pactl for the description of the target sink.
      2. Try to match that description against sounddevice device names.
      3. Fall back to MAC-segment and prefix heuristics.
      4. Last resort: return the default device.
    """
    from sendspin.audio import query_devices

    devices = query_devices()
    if not sink_name:
        return next((d for d in devices if d.is_default), None)

    # Log available devices once for diagnostics
    logger.debug(
        "resolve_audio_device_for_sink(%s): available devices: %s",
        sink_name, [d.name for d in devices],
    )

    # 1. Exact match on sink name (rare but possible with PipeWire)
    for dev in devices:
        if dev.name == sink_name:
            logger.info("Audio device matched by exact sink name: %s", dev.name)
            return dev

    # 2. Match via PA description (most reliable with PulseAudio)
    description = _get_sink_description(sink_name)
    if description:
        logger.debug("Sink description for %s: %s", sink_name, description)
        desc_lower = description.lower()
        for dev in devices:
            if dev.name.lower() == desc_lower:
                logger.info("Audio device matched by description: %s", dev.name)
                return dev
        # Partial description match (truncated names)
        for dev in devices:
            if desc_lower in dev.name.lower() or dev.name.lower() in desc_lower:
                logger.info("Audio device partial-matched by description: %s (desc=%s)", dev.name, description)
                return dev

    # 3. MAC-segment match: 'bluez_output.AA_BB_CC_DD_EE_FF.1' → 'AA_BB_CC_DD_EE_FF'
    parts = sink_name.split('.')
    mac_segment = parts[1] if len(parts) >= 2 else ''
    if mac_segment:
        for dev in devices:
            if mac_segment.lower() in dev.name.lower():
                logger.info("Audio device matched by MAC segment: %s", dev.name)
                return dev

    # 4. Prefix match
    for dev in devices:
        if dev.name.startswith(sink_name[:20]):
            logger.info("Audio device matched by prefix: %s", dev.name)
            return dev

    logger.warning(
        "No audio device found for sink %s (description=%s) — falling back to default",
        sink_name, description,
    )
    return next((d for d in devices if d.is_default), None)
