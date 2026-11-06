"""The source inputs the operator can choose: Bluetooth bonds and local audio inputs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable

    from sendspin_bridge.services.source.state import SourceDevice

__all__ = [
    "DEFAULT_INPUT_VOLUME",
    "NO_SOURCE",
    "SourceInput",
    "bluetooth_id",
    "build_inputs",
    "headset_mic_id",
    "input_audio",
    "local_candidates",
    "local_id",
    "parse_source_id",
]

SourceKind = Literal["bluetooth", "local", "headset_microphone"]

#: Explicit "no active source"; distinct from the legacy ``None`` auto-Bluetooth policy.
NO_SOURCE = "none"

#: A source the operator has never touched runs at the audio server's default gain.
DEFAULT_INPUT_VOLUME = 100


@dataclass(frozen=True, slots=True)
class SourceInput:
    """One selectable source input, ready for the status/read model."""

    id: str
    kind: SourceKind
    name: str
    description: str = ""
    active: bool = False
    available: bool = True
    connected: bool = False
    monitor: bool = False
    mac: str | None = None
    node: str | None = None
    volume: int = DEFAULT_INPUT_VOLUME
    muted: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "name": self.name,
            "description": self.description,
            "active": self.active,
            "available": self.available,
            "connected": self.connected,
            "monitor": self.monitor,
            "mac": self.mac,
            "node": self.node,
            "volume": self.volume,
            "muted": self.muted,
        }


def input_audio(config: dict[str, Any] | None, source_id: str) -> tuple[int, bool]:
    """The saved ``(volume, muted)`` of one input id; full scale and unmuted when unset."""
    raw = (config or {}).get("SENDSPIN_SOURCE_INPUT_STATE")
    entry = raw.get(source_id) if isinstance(raw, dict) else None
    volume = entry.get("volume") if isinstance(entry, dict) else None
    muted = entry.get("muted") if isinstance(entry, dict) else None
    if not isinstance(volume, int) or isinstance(volume, bool) or not 0 <= volume <= 100:
        volume = DEFAULT_INPUT_VOLUME
    if not isinstance(muted, bool):
        muted = False
    return volume, muted


def bluetooth_id(mac: str) -> str:
    return f"bluetooth:{mac.upper()}"


def local_id(node: str) -> str:
    return f"local:{node}"


def headset_mic_id(mac: str) -> str:
    return f"headset-mic:{mac.upper()}"


def parse_source_id(source_id: str) -> tuple[SourceKind, str] | None:
    """Split ``bluetooth:<MAC>`` / ``local:<node>``; None when malformed."""
    raw = str(source_id or "").strip()
    kind, sep, value = raw.partition(":")
    if not sep or not value:
        return None
    if kind == "bluetooth":
        return "bluetooth", value.upper()
    if kind == "local":
        return "local", value
    if kind == "headset-mic":
        return "headset_microphone", value.upper()
    return None


def local_profiles(config: dict[str, Any] | None) -> list[dict[str, str]]:
    """Normalised local profiles from config, tolerating older/hand-edited files."""
    profiles: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in (config or {}).get("SENDSPIN_SOURCES") or ():
        if not isinstance(item, dict):
            continue
        node = str(item.get("node") or "").strip()
        if not node or node in seen:
            continue
        seen.add(node)
        profiles.append(
            {
                "id": local_id(node),
                "kind": "local",
                "node": node,
                "name": str(item.get("name") or "").strip(),
            }
        )
    return profiles


def _audio_by_name(audio_sources: Iterable[Any] | None) -> dict[str, Any]:
    return {str(getattr(source, "name", "") or ""): source for source in audio_sources or ()}


def local_candidates(
    audio_sources: Iterable[Any] | None,
    *,
    configured: Collection[str] = (),
    include_monitors: bool = False,
) -> list[SourceInput]:
    """Discovered local inputs, excluding bridge internals and Bluetooth endpoints."""
    found: list[SourceInput] = []
    for source in audio_sources or ():
        node = str(getattr(source, "name", "") or "").strip()
        if not node or node.startswith("sendspin_") or node.startswith(("bluez_source.", "bluez_input.")):
            continue
        monitor_of = getattr(source, "monitor_of", None)
        if monitor_of and not include_monitors:
            continue
        description = str(getattr(source, "description", "") or "").strip()
        found.append(
            SourceInput(
                id=local_id(node),
                kind="local",
                name=description or node,
                description=description,
                available=True,
                monitor=bool(monitor_of),
                node=node,
            )
        )
    return found


def build_inputs(
    config: dict[str, Any] | None,
    *,
    active_input: str | None = None,
    bluetooth: Iterable[SourceDevice] | None = None,
    audio_sources: Iterable[Any] | None = None,
    include_monitors: bool = False,
    include_candidates: bool = False,
    headsets: Iterable[dict[str, Any]] | None = None,
) -> list[SourceInput]:
    """All selectable source inputs: Bluetooth bonds, headset microphones, then local profiles."""
    active = str(active_input or "")
    audio = _audio_by_name(audio_sources)
    inputs: list[SourceInput] = []
    for device in bluetooth or ():
        source_id = bluetooth_id(device.mac)
        volume, muted = input_audio(config, source_id)
        inputs.append(
            SourceInput(
                id=source_id,
                kind="bluetooth",
                name=device.name or device.mac,
                description=device.mac,
                active=active == source_id,
                available=True,
                connected=device.connected,
                mac=device.mac,
                volume=volume,
                muted=muted,
            )
        )
    for headset in headsets or ():
        mac = str(headset.get("mac") or "").upper()
        if not mac:
            continue
        source_id = headset_mic_id(mac)
        volume, muted = input_audio(config, source_id)
        inputs.append(
            SourceInput(
                id=source_id,
                kind="headset_microphone",
                name=str(headset.get("name") or mac),
                description="HFP microphone",
                active=active == source_id,
                available=bool(headset.get("hfp_available", True)),
                mac=mac,
                volume=volume,
                muted=muted,
            )
        )
    for profile in local_profiles(config):
        node = profile["node"]
        source = audio.get(node)
        # No audio list means "status snapshot, not a live probe": keep the
        # saved profile selectable; the capture client reports input_missing.
        available = True if audio_sources is None else source is not None
        volume, muted = input_audio(config, profile["id"])
        inputs.append(
            SourceInput(
                id=profile["id"],
                kind="local",
                name=profile["name"] or (str(getattr(source, "description", "") or "").strip() or node),
                description=str(getattr(source, "description", "") or "").strip(),
                active=active == profile["id"],
                available=available,
                monitor=bool(getattr(source, "monitor_of", None)),
                node=node,
                volume=volume,
                muted=muted,
            )
        )
    # Candidates already configured must not be offered twice in the add modal.
    if include_candidates:
        configured = {entry.id for entry in inputs}
        for candidate in local_candidates(audio_sources, include_monitors=include_monitors):
            if candidate.id not in configured:
                inputs.append(candidate)
    return inputs
