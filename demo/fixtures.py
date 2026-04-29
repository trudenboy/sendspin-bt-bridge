"""Canonical demo fixtures for the local nine-player screenshot stand."""

from __future__ import annotations

import re
from urllib.parse import quote

from sendspin_bridge.config import VERSION

DEMO_DISPLAY_VERSION = f"{VERSION}-demo"


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
SECOND_GROUP_TRACK_INDEX = 5
SOLO_TRACK_INDEX = 6

MAIN_FLOOR_TRACK = DEMO_TRACKS[MAIN_FLOOR_TRACK_INDEX]
SECOND_GROUP_TRACK = DEMO_TRACKS[SECOND_GROUP_TRACK_INDEX]
SOLO_TRACK = DEMO_TRACKS[SOLO_TRACK_INDEX]


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
    {
        "mac": "AA:BB:CC:DD:EE:07",
        "player_name": "Guest Room",
        "adapter": "hci0",
        "enabled": True,
        "released": True,
        "listen_port": 8934,
        "preferred_format": "aac:44100:16:2",
    },
    {
        "mac": "AA:BB:CC:DD:EE:08",
        "player_name": "Bathroom",
        "adapter": "hci1",
        "enabled": True,
        "listen_port": 8935,
        "preferred_format": "mp3:44100:16:2",
    },
    {
        "mac": "AA:BB:CC:DD:EE:09",
        "player_name": "Balcony",
        "adapter": "hci0",
        "enabled": True,
        "listen_port": 8936,
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
        "muted": True,
        "battery_level": None,
        "audio_format": "FLAC 48000Hz 24bit 2ch",
        "current_track": MAIN_FLOOR_TRACK["title"],
        "current_artist": MAIN_FLOOR_TRACK["artist"],
        "track_duration_ms": MAIN_FLOOR_TRACK["duration_ms"],
        "track_progress_ms": 68000,
        "group_id": "syncgroup_main_floor",
        "group_name": "Main Floor",
        "reanchor_count": 5,
        "last_reanchor_at": 1012.4,
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
        "reanchor_count": 112,
        "last_sync_error_ms": 286.4,
        "last_reanchor_at": 1021.8,
        "reanchoring": True,
    },
    "AA:BB:CC:DD:EE:03": {
        "bluetooth_connected": True,
        "server_connected": True,
        "connected": True,
        "playing": True,
        "volume": 41,
        "muted": False,
        "battery_level": 17,
        "audio_format": "AAC 44100Hz 16bit 2ch",
        "current_track": MAIN_FLOOR_TRACK["title"],
        "current_artist": MAIN_FLOOR_TRACK["artist"],
        "track_duration_ms": MAIN_FLOOR_TRACK["duration_ms"],
        "track_progress_ms": 68000,
        "group_id": "syncgroup_main_floor",
        "group_name": "Main Floor",
        "reanchor_count": 15,
        "last_reanchor_at": 998.1,
    },
    "AA:BB:CC:DD:EE:04": {
        "bluetooth_connected": True,
        "server_connected": True,
        "connected": True,
        "playing": True,
        "volume": 47,
        "muted": False,
        "battery_level": None,
        "audio_format": "AAC 44100Hz 16bit 2ch",
        "current_track": SECOND_GROUP_TRACK["title"],
        "current_artist": SECOND_GROUP_TRACK["artist"],
        "track_duration_ms": SECOND_GROUP_TRACK["duration_ms"],
        "track_progress_ms": 131000,
        "group_id": "syncgroup_focus_zone",
        "group_name": "Focus Zone",
    },
    "AA:BB:CC:DD:EE:05": {
        "bluetooth_connected": False,
        "server_connected": False,
        "connected": False,
        "playing": False,
        "volume": 28,
        "muted": False,
        "battery_level": 9,
        "audio_format": "MP3 44100Hz 16bit 2ch",
        "current_track": SECOND_GROUP_TRACK["title"],
        "current_artist": SECOND_GROUP_TRACK["artist"],
        "track_duration_ms": SECOND_GROUP_TRACK["duration_ms"],
        "track_progress_ms": 131000,
        "group_id": "syncgroup_focus_zone",
        "group_name": "Focus Zone",
        "reconnecting": True,
        "reconnect_attempt": 2,
    },
    "AA:BB:CC:DD:EE:06": {
        "bluetooth_connected": True,
        "server_connected": True,
        "connected": True,
        "playing": False,
        "volume": 36,
        "muted": True,
        "battery_level": 41,
        "audio_format": "FLAC 44100Hz 16bit 2ch",
        "current_track": SOLO_TRACK["title"],
        "current_artist": SOLO_TRACK["artist"],
        "track_duration_ms": SOLO_TRACK["duration_ms"],
        "track_progress_ms": 42000,
        "buffering": True,
    },
    "AA:BB:CC:DD:EE:07": {
        "bluetooth_connected": False,
        "server_connected": False,
        "connected": False,
        "playing": False,
        "volume": 34,
        "muted": False,
        "battery_level": None,
        "audio_format": "AAC 44100Hz 16bit 2ch",
        "bt_management_enabled": False,
        "bt_released_by": "user",
    },
    "AA:BB:CC:DD:EE:08": {
        "bluetooth_connected": True,
        "server_connected": True,
        "connected": True,
        "playing": False,
        "volume": 61,
        "muted": True,
        "battery_level": None,
        "audio_format": "MP3 44100Hz 16bit 2ch",
        "current_track": "Dreams",
        "current_artist": "Fleetwood Mac",
        "track_duration_ms": 257000,
        "track_progress_ms": 0,
    },
    "AA:BB:CC:DD:EE:09": {
        "bluetooth_connected": True,
        "server_connected": True,
        "connected": True,
        "playing": False,
        "volume": 22,
        "muted": False,
        "battery_level": 48,
        "audio_format": "FLAC 44100Hz 16bit 2ch",
        "current_track": "Nightcall",
        "current_artist": "Kavinsky",
        "track_duration_ms": 257000,
        "track_progress_ms": 201000,
        "stopping": True,
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
    {"mac": "AA:BB:CC:DD:EE:07", "name": "Guest Room", "adapter": "hci0"},
    {"mac": "AA:BB:CC:DD:EE:08", "name": "Bathroom", "adapter": "hci1"},
    {"mac": "AA:BB:CC:DD:EE:09", "name": "Balcony", "adapter": "hci0"},
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
    {"mac": "AA:BB:CC:DD:EE:05", "name": "Patio", "connected": False},
    {"mac": "AA:BB:CC:DD:EE:06", "name": "Bedroom", "connected": True},
    {"mac": "AA:BB:CC:DD:EE:07", "name": "Guest Room", "connected": False},
    {"mac": "AA:BB:CC:DD:EE:08", "name": "Bathroom", "connected": True},
    {"mac": "AA:BB:CC:DD:EE:09", "name": "Balcony", "connected": True},
    {"mac": "11:22:33:44:55:04", "name": "Sony WH-1000XM4", "connected": False},
    {"mac": "11:22:33:44:55:05", "name": "Marshall Emberton", "connected": False},
]

# ---------------------------------------------------------------------------
# Demo logs / diagnostics fixtures
# ---------------------------------------------------------------------------
DEMO_LOG_LINES = [
    "2026-03-18 21:49:07,102 - demo - INFO - Demo runtime bootstrapped with 9 fixture devices",
    "2026-03-18 21:49:07,188 - demo.bt_manager - INFO - [demo] Audio configured: bluez_output.AA_BB_CC_DD_EE_01.1",
    "2026-03-18 21:49:07,322 - demo - INFO - [demo] MA monitor connected (simulated)",
    "2026-03-18 21:49:08,041 - demo-sim - INFO - [demo-sim] Canonical simulator started for 9 device(s)",
    "2026-03-18 21:49:09,552 - demo - WARNING - daemon stderr: simulated sink latency spike on Patio",
    "2026-03-18 21:49:10,004 - demo - ERROR - demo watchdog noticed a stalled stream and marked diagnostics degraded",
]

DEMO_BT_DEVICE_INFO = [
    {
        "mac": str(device["mac"]),
        "name": str(device["name"]),
        "paired": "yes",
        "trusted": "yes",
        "connected": "yes" if bool(device.get("connected")) else "no",
        "bonded": "yes",
        "blocked": "no",
        "icon": "audio-card",
    }
    for device in DEMO_PAIRED_DEVICES
    if str(device["mac"]).startswith("AA:BB:CC:DD:EE:")
]

DEMO_PORTAUDIO_DEVICES = [
    {"index": 0, "name": "Demo Bluetooth Mix", "is_default": True, "output_channels": 2},
    {"index": 1, "name": "Demo Monitor Output", "is_default": False, "output_channels": 2},
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
                "state": "playing",
                "volume": 41,
                "available": True,
            },
        ],
    },
    {
        "id": "syncgroup_focus_zone",
        "name": "Focus Zone",
        "members": [
            {
                "id": demo_player_id_for_name("Office"),
                "name": "Office",
                "state": "playing",
                "volume": 47,
                "available": True,
            },
            {
                "id": demo_player_id_for_name("Patio"),
                "name": "Patio",
                "state": "reconnecting",
                "volume": 28,
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
    "office": {"id": "syncgroup_focus_zone", "name": "Focus Zone"},
    "patio": {"id": "syncgroup_focus_zone", "name": "Focus Zone"},
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
    "syncgroup_focus_zone": _ma_now_playing_entry(
        "syncgroup_focus_zone",
        "Focus Zone",
        SECOND_GROUP_TRACK_INDEX,
        state="playing",
        connected=True,
        elapsed_seconds=131,
        shuffle=True,
        repeat="all",
    ),
    demo_player_id_for_name("Bedroom"): _ma_now_playing_entry(
        demo_player_id_for_name("Bedroom"),
        "Bedroom",
        SOLO_TRACK_INDEX,
        state="playing",
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
