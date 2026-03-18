"""Canonical demo fixtures for the local six-player screenshot stand."""

from __future__ import annotations

import re
from urllib.parse import quote

from config import VERSION


# ---------------------------------------------------------------------------
# Artwork + track helpers
# ---------------------------------------------------------------------------
def _artwork_data_uri(title: str, subtitle: str, background: str, accent: str) -> str:
    svg = f"""
    <svg xmlns=\"http://www.w3.org/2000/svg\" width=\"640\" height=\"640\" viewBox=\"0 0 640 640\">
      <defs>
        <linearGradient id=\"g\" x1=\"0%\" y1=\"0%\" x2=\"100%\" y2=\"100%\">
          <stop offset=\"0%\" stop-color=\"{background}\"/>
          <stop offset=\"100%\" stop-color=\"{accent}\"/>
        </linearGradient>
      </defs>
      <rect width=\"640\" height=\"640\" rx=\"64\" fill=\"url(#g)\"/>
      <circle cx=\"510\" cy=\"128\" r=\"72\" fill=\"rgba(255,255,255,0.14)\"/>
      <circle cx=\"132\" cy=\"516\" r=\"92\" fill=\"rgba(255,255,255,0.12)\"/>
      <text x=\"64\" y=\"340\" fill=\"#ffffff\" font-family=\"Inter,Arial,sans-serif\" font-size=\"64\" font-weight=\"700\">{title}</text>
      <text x=\"64\" y=\"404\" fill=\"rgba(255,255,255,0.88)\" font-family=\"Inter,Arial,sans-serif\" font-size=\"30\">{subtitle}</text>
      <text x=\"64\" y=\"560\" fill=\"rgba(255,255,255,0.72)\" font-family=\"Inter,Arial,sans-serif\" font-size=\"24\" letter-spacing=\"8\">SENDSPIN DEMO</text>
    </svg>
    """.strip()
    return f"data:image/svg+xml;utf8,{quote(svg)}"


def _track(title: str, artist: str, album: str, duration_ms: int, background: str, accent: str) -> dict[str, str | int]:
    return {
        "title": title,
        "artist": artist,
        "album": album,
        "duration_ms": duration_ms,
        "image_url": _artwork_data_uri(title, f"{artist} · {album}", background, accent),
    }


DEMO_TRACKS = [
    _track("Midnight City", "M83", "Hurry Up, We're Dreaming", 244000, "#2d1b69", "#ff6fd8"),
    _track("The Less I Know the Better", "Tame Impala", "Currents", 216000, "#0a6c74", "#ffd166"),
    _track("Texas Sun", "Khruangbin & Leon Bridges", "Texas Sun", 252000, "#7b341e", "#f6ad55"),
    _track("On Hold", "The xx", "I See You", 224000, "#1a365d", "#63b3ed"),
    _track("Nightcall", "Kavinsky", "OutRun", 257000, "#1f1235", "#f687b3"),
    _track("Everything In Its Right Place", "Radiohead", "Kid A", 251000, "#1f2937", "#34d399"),
    _track("Sunset Lover", "Petit Biscuit", "Presence", 197000, "#553c9a", "#f6e05e"),
    _track("Dreams", "Fleetwood Mac", "Rumours", 257000, "#234e52", "#81e6d9"),
]

MAIN_FLOOR_TRACK_INDEX = 3
OFFICE_TRACK_INDEX = 5
PATIO_TRACK_INDEX = 6

MAIN_FLOOR_TRACK = DEMO_TRACKS[MAIN_FLOOR_TRACK_INDEX]
OFFICE_TRACK = DEMO_TRACKS[OFFICE_TRACK_INDEX]
PATIO_TRACK = DEMO_TRACKS[PATIO_TRACK_INDEX]


def demo_track_summary(track_index: int) -> dict[str, str]:
    """Return title/artist/album metadata for a demo queue item."""
    track = DEMO_TRACKS[track_index % len(DEMO_TRACKS)]
    return {
        "track": str(track["title"]),
        "artist": str(track["artist"]),
        "album": str(track["album"]),
    }


def demo_queue_neighbors(track_index: int) -> dict[str, str]:
    """Return previous/next queue metadata for the selected demo track."""
    result: dict[str, str] = {}
    if track_index > 0:
        previous = demo_track_summary(track_index - 1)
        result.update({f"prev_{key}": value for key, value in previous.items()})
    if track_index + 1 < len(DEMO_TRACKS):
        following = demo_track_summary(track_index + 1)
        result.update({f"next_{key}": value for key, value in following.items()})
    return result


def demo_player_id_for_name(player_name: str) -> str:
    """Return the canonical demo MA player/queue id for a fixture player name."""
    base_name = player_name.split(" @ ", 1)[0].strip()
    safe_id = re.sub(r"-+", "-", "".join(c if c.isalnum() or c == "-" else "-" for c in base_name.lower())).strip("-")
    return f"sendspin-demo-{safe_id or 'player'}"


def _next_demo_version(version: str) -> str:
    """Derive a deterministic, obviously-newer version for the demo update badge."""
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)", version)
    if not match:
        return version
    major, minor, patch = (int(part) for part in match.groups())
    return f"{major}.{minor}.{patch + 1}"


# ---------------------------------------------------------------------------
# Bluetooth adapters (shown in config + diagnostics)
# ---------------------------------------------------------------------------
DEMO_ADAPTERS = [
    {
        "id": "hci0",
        "mac": "00:1A:7D:DA:71:01",
        "name": "Living Room USB Adapter",
        "powered": True,
        "discoverable": False,
        "pairable": True,
    },
    {
        "id": "hci1",
        "mac": "00:1A:7D:DA:71:02",
        "name": "Kitchen & Patio Controller",
        "powered": True,
        "discoverable": False,
        "pairable": True,
    },
    {
        "id": "hci2",
        "mac": "00:1A:7D:DA:71:03",
        "name": "Office Desk Bluetooth",
        "powered": True,
        "discoverable": False,
        "pairable": True,
    },
]
DEMO_ADAPTERS_BY_ID = {str(adapter["id"]): adapter for adapter in DEMO_ADAPTERS}
DEMO_ADAPTERS_BY_MAC = {str(adapter["mac"]).upper(): adapter for adapter in DEMO_ADAPTERS}
DEMO_ADAPTER_NAMES = [str(adapter["name"]) for adapter in DEMO_ADAPTERS]
DEMO_ADAPTER_MAC = str(DEMO_ADAPTERS[0]["mac"])
DEMO_ADAPTER_INFO = DEMO_ADAPTERS[0]


def get_demo_adapter(identifier: str | None = None) -> dict:
    """Return the configured demo adapter for a given hci id or MAC."""
    normalized = str(identifier or "").strip()
    if not normalized:
        return DEMO_ADAPTERS[0]
    return DEMO_ADAPTERS_BY_ID.get(normalized) or DEMO_ADAPTERS_BY_MAC.get(normalized.upper()) or DEMO_ADAPTERS[0]


# ---------------------------------------------------------------------------
# Pre-configured Bluetooth speakers (shown on the dashboard)
# ---------------------------------------------------------------------------
DEMO_DEVICES = [
    {
        "mac": "AA:BB:CC:DD:EE:01",
        "player_name": "Living Room",
        "adapter": "hci0",
        "enabled": True,
        "listen_port": 8928,
        "preferred_format": "flac:48000:24:2",
    },
    {
        "mac": "AA:BB:CC:DD:EE:02",
        "player_name": "Kitchen",
        "adapter": "hci1",
        "enabled": True,
        "listen_port": 8929,
        "preferred_format": "flac:44100:16:2",
    },
    {
        "mac": "AA:BB:CC:DD:EE:03",
        "player_name": "Studio",
        "adapter": "hci2",
        "enabled": True,
        "listen_port": 8930,
        "preferred_format": "aac:44100:16:2",
    },
    {
        "mac": "AA:BB:CC:DD:EE:04",
        "player_name": "Office",
        "adapter": "hci2",
        "enabled": True,
        "listen_port": 8931,
        "preferred_format": "aac:44100:16:2",
    },
    {
        "mac": "AA:BB:CC:DD:EE:05",
        "player_name": "Patio",
        "adapter": "hci1",
        "enabled": True,
        "listen_port": 8932,
        "preferred_format": "mp3:44100:16:2",
    },
    {
        "mac": "AA:BB:CC:DD:EE:06",
        "player_name": "Bedroom",
        "adapter": "hci2",
        "enabled": True,
        "listen_port": 8933,
        "preferred_format": "flac:44100:16:2",
    },
]

# Initial status per device (keyed by MAC)
DEMO_DEVICE_STATUS: dict[str, dict] = {
    "AA:BB:CC:DD:EE:01": {
        "bluetooth_connected": True,
        "server_connected": True,
        "connected": True,
        "playing": True,
        "volume": 72,
        "muted": False,
        "battery_level": 88,
        "audio_format": "FLAC 48000Hz 24bit 2ch",
        "current_track": MAIN_FLOOR_TRACK["title"],
        "current_artist": MAIN_FLOOR_TRACK["artist"],
        "track_duration_ms": MAIN_FLOOR_TRACK["duration_ms"],
        "track_progress_ms": 68000,
        "group_id": "syncgroup_main_floor",
        "group_name": "Main Floor",
    },
    "AA:BB:CC:DD:EE:02": {
        "bluetooth_connected": True,
        "server_connected": True,
        "connected": True,
        "playing": True,
        "volume": 50,
        "muted": False,
        "battery_level": 45,
        "audio_format": "FLAC 44100Hz 16bit 2ch",
        "current_track": MAIN_FLOOR_TRACK["title"],
        "current_artist": MAIN_FLOOR_TRACK["artist"],
        "track_duration_ms": MAIN_FLOOR_TRACK["duration_ms"],
        "track_progress_ms": 68000,
        "group_id": "syncgroup_main_floor",
        "group_name": "Main Floor",
    },
    "AA:BB:CC:DD:EE:03": {
        "bluetooth_connected": True,
        "server_connected": True,
        "connected": True,
        "playing": False,
        "volume": 41,
        "muted": False,
        "battery_level": 37,
        "audio_format": "AAC 44100Hz 16bit 2ch",
        "current_track": MAIN_FLOOR_TRACK["title"],
        "current_artist": MAIN_FLOOR_TRACK["artist"],
        "track_duration_ms": MAIN_FLOOR_TRACK["duration_ms"],
        "track_progress_ms": 68000,
        "group_id": "syncgroup_main_floor",
        "group_name": "Main Floor",
    },
    "AA:BB:CC:DD:EE:04": {
        "bluetooth_connected": True,
        "server_connected": True,
        "connected": True,
        "playing": True,
        "volume": 47,
        "muted": False,
        "battery_level": 53,
        "audio_format": "AAC 44100Hz 16bit 2ch",
        "current_track": OFFICE_TRACK["title"],
        "current_artist": OFFICE_TRACK["artist"],
        "track_duration_ms": OFFICE_TRACK["duration_ms"],
        "track_progress_ms": 131000,
    },
    "AA:BB:CC:DD:EE:05": {
        "bluetooth_connected": True,
        "server_connected": True,
        "connected": True,
        "playing": False,
        "volume": 28,
        "muted": True,
        "battery_level": 74,
        "audio_format": "MP3 44100Hz 16bit 2ch",
        "current_track": PATIO_TRACK["title"],
        "current_artist": PATIO_TRACK["artist"],
        "track_duration_ms": PATIO_TRACK["duration_ms"],
        "track_progress_ms": 42000,
    },
    "AA:BB:CC:DD:EE:06": {
        "bluetooth_connected": False,
        "server_connected": False,
        "connected": False,
        "playing": False,
        "volume": 100,
        "muted": False,
        "battery_level": None,
    },
}

# ---------------------------------------------------------------------------
# BT scan results — mix of configured and discoverable audio devices
# ---------------------------------------------------------------------------
DEMO_SCAN_RESULTS = [
    {"mac": "AA:BB:CC:DD:EE:01", "name": "Living Room", "adapter": "hci0"},
    {"mac": "AA:BB:CC:DD:EE:02", "name": "Kitchen", "adapter": "hci1"},
    {"mac": "AA:BB:CC:DD:EE:03", "name": "Studio", "adapter": "hci2"},
    {"mac": "AA:BB:CC:DD:EE:04", "name": "Office", "adapter": "hci2"},
    {"mac": "AA:BB:CC:DD:EE:05", "name": "Patio", "adapter": "hci1"},
    {"mac": "AA:BB:CC:DD:EE:06", "name": "Bedroom", "adapter": "hci2"},
    {"mac": "11:22:33:44:55:01", "name": "Guest Speaker", "adapter": "hci0"},
    {"mac": "11:22:33:44:55:02", "name": "Desk Headphones", "adapter": "hci2"},
    {"mac": "11:22:33:44:55:03", "name": "Portable Boom", "adapter": "hci1"},
]

# ---------------------------------------------------------------------------
# Paired devices (returned by /api/bt/paired)
# ---------------------------------------------------------------------------
DEMO_PAIRED_DEVICES = [
    {"mac": "AA:BB:CC:DD:EE:01", "name": "Living Room", "connected": True},
    {"mac": "AA:BB:CC:DD:EE:02", "name": "Kitchen", "connected": True},
    {"mac": "AA:BB:CC:DD:EE:03", "name": "Studio", "connected": True},
    {"mac": "AA:BB:CC:DD:EE:04", "name": "Office", "connected": True},
    {"mac": "AA:BB:CC:DD:EE:05", "name": "Patio", "connected": True},
    {"mac": "AA:BB:CC:DD:EE:06", "name": "Bedroom", "connected": False},
    {"mac": "11:22:33:44:55:04", "name": "Sony WH-1000XM4", "connected": False},
    {"mac": "11:22:33:44:55:05", "name": "Marshall Emberton", "connected": False},
]

# ---------------------------------------------------------------------------
# Music Assistant mock data
# ---------------------------------------------------------------------------
DEMO_MA_URL = "http://demo-ma.local:8095"
DEMO_MA_TOKEN = "demo-token-not-real"

# Syncgroups returned by discover_ma_groups
DEMO_MA_ALL_GROUPS = [
    {
        "id": "syncgroup_main_floor",
        "name": "Main Floor",
        "members": [
            {
                "id": demo_player_id_for_name("Living Room"),
                "name": "Living Room",
                "state": "playing",
                "volume": 72,
                "available": True,
            },
            {
                "id": demo_player_id_for_name("Kitchen"),
                "name": "Kitchen",
                "state": "playing",
                "volume": 50,
                "available": True,
            },
            {
                "id": demo_player_id_for_name("Studio"),
                "name": "Studio",
                "state": "idle",
                "volume": 41,
                "available": True,
            },
        ],
    },
]

# name_map returned by discover_ma_groups (player_name.lower → group info)
DEMO_MA_NAME_MAP: dict[str, dict] = {
    "living room": {"id": "syncgroup_main_floor", "name": "Main Floor"},
    "kitchen": {"id": "syncgroup_main_floor", "name": "Main Floor"},
    "studio": {"id": "syncgroup_main_floor", "name": "Main Floor"},
}


def _ma_now_playing_entry(
    queue_id: str,
    queue_name: str,
    track_index: int,
    *,
    state: str,
    connected: bool,
    elapsed_seconds: int,
    shuffle: bool = False,
    repeat: str = "off",
) -> dict[str, str | int | bool | float]:
    track = DEMO_TRACKS[track_index]
    result: dict[str, str | int | bool | float] = {
        "connected": connected,
        "state": state,
        "track": track["title"],
        "artist": track["artist"],
        "album": track["album"],
        "image_url": track["image_url"],
        "elapsed": elapsed_seconds,
        "elapsed_updated_at": 0,
        "duration": int(track["duration_ms"]) / 1000,
        "shuffle": shuffle,
        "repeat": repeat,
        "queue_index": track_index,
        "queue_total": len(DEMO_TRACKS),
        "syncgroup_id": queue_id,
        "syncgroup_name": queue_name,
    }
    result.update(demo_queue_neighbors(track_index))
    return result


# Now-playing data per queue (syncgroups and solo demo players)
DEMO_MA_NOW_PLAYING = {
    "syncgroup_main_floor": _ma_now_playing_entry(
        "syncgroup_main_floor",
        "Main Floor",
        MAIN_FLOOR_TRACK_INDEX,
        state="playing",
        connected=True,
        elapsed_seconds=68,
    ),
    demo_player_id_for_name("Office"): _ma_now_playing_entry(
        demo_player_id_for_name("Office"),
        "Office",
        OFFICE_TRACK_INDEX,
        state="playing",
        connected=True,
        elapsed_seconds=131,
        shuffle=True,
        repeat="all",
    ),
    demo_player_id_for_name("Patio"): _ma_now_playing_entry(
        demo_player_id_for_name("Patio"),
        "Patio",
        PATIO_TRACK_INDEX,
        state="paused",
        connected=True,
        elapsed_seconds=42,
    ),
}

# Server info returned by MA discovery/validate
DEMO_MA_SERVER_INFO = {
    "url": DEMO_MA_URL,
    "version": VERSION,
    "server_id": "demo-server-id",
    "schema_version": 25,
    "onboard_done": True,
    "homeassistant_addon": False,
}

DEMO_UPDATE_VERSION = _next_demo_version(VERSION)
DEMO_UPDATE_INFO = {
    "version": DEMO_UPDATE_VERSION,
    "tag": f"v{DEMO_UPDATE_VERSION}",
    "current_version": VERSION,
    "url": f"https://github.com/trudenboy/sendspin-bt-bridge/releases/tag/v{DEMO_UPDATE_VERSION}",
    "published_at": "2026-03-18T09:00:00Z",
    "body": "\n".join(
        [
            "## What's Changed",
            "",
            "- Refined the screenshot demo so the list layout is the default presentation.",
            "- Added richer now-playing fixtures with album artwork plus previous and next queue items.",
            "- Seeded update state with visible release notes for local screenshot capture.",
        ]
    ),
    "channel": "stable",
    "target_ref": f"v{DEMO_UPDATE_VERSION}",
    "prerelease": False,
}
