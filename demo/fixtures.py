"""Demo mode fixtures — fake devices, scan results, MA groups, tracks."""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Pre-configured Bluetooth speakers (shown on the dashboard)
# ---------------------------------------------------------------------------
DEMO_DEVICES = [
    {
        "mac": "AA:BB:CC:DD:EE:01",
        "player_name": "Living Room",
        "adapter": "",
        "enabled": True,
        "listen_port": 8928,
        "preferred_format": "flac:44100:16:2",
    },
    {
        "mac": "AA:BB:CC:DD:EE:02",
        "player_name": "Kitchen",
        "adapter": "",
        "enabled": True,
        "listen_port": 8929,
        "preferred_format": "flac:44100:16:2",
    },
    {
        "mac": "AA:BB:CC:DD:EE:03",
        "player_name": "Bedroom",
        "adapter": "",
        "enabled": True,
        "listen_port": 8930,
        "preferred_format": "flac:44100:16:2",
    },
    {
        "mac": "AA:BB:CC:DD:EE:04",
        "player_name": "Garage",
        "adapter": "",
        "enabled": True,
        "listen_port": 8931,
        "preferred_format": "flac:44100:16:2",
    },
    {
        "mac": "AA:BB:CC:DD:EE:05",
        "player_name": "Bathroom",
        "adapter": "",
        "enabled": True,
        "listen_port": 8932,
        "preferred_format": "flac:44100:16:2",
    },
]

# Initial status per device (keyed by MAC)
DEMO_DEVICE_STATUS: dict[str, dict] = {
    "AA:BB:CC:DD:EE:01": {
        "bluetooth_connected": True,
        "server_connected": True,
        "playing": True,
        "volume": 75,
        "muted": False,
        "battery_level": 80,
        "audio_format": "FLAC 44100Hz 16bit 2ch",
        "current_track": "Bohemian Rhapsody",
        "current_artist": "Queen",
        "track_duration_ms": 354000,
        "track_progress_ms": 120000,
        "group_id": "syncgroup_downstairs",
        "group_name": "Downstairs",
    },
    "AA:BB:CC:DD:EE:02": {
        "bluetooth_connected": True,
        "server_connected": True,
        "playing": False,
        "volume": 50,
        "muted": False,
        "battery_level": 45,
        "audio_format": "FLAC 44100Hz 16bit 2ch",
        "current_track": None,
        "current_artist": None,
        "group_id": "syncgroup_downstairs",
        "group_name": "Downstairs",
    },
    "AA:BB:CC:DD:EE:03": {
        "bluetooth_connected": False,
        "server_connected": False,
        "playing": False,
        "volume": 100,
        "muted": False,
        "battery_level": None,
    },
    "AA:BB:CC:DD:EE:04": {
        "bluetooth_connected": True,
        "server_connected": True,
        "playing": True,
        "volume": 60,
        "muted": False,
        "battery_level": 92,
        "audio_format": "FLAC 44100Hz 16bit 2ch",
        "current_track": "Hotel California",
        "current_artist": "Eagles",
        "track_duration_ms": 391000,
        "track_progress_ms": 45000,
        "group_id": "syncgroup_garage",
        "group_name": "Garage",
    },
    "AA:BB:CC:DD:EE:05": {
        "bluetooth_connected": True,
        "server_connected": True,
        "playing": False,
        "volume": 35,
        "muted": True,
        "battery_level": 15,
        "audio_format": "FLAC 44100Hz 16bit 2ch",
        "current_track": None,
        "current_artist": None,
        "group_id": "syncgroup_all",
        "group_name": "Whole House",
    },
}

# ---------------------------------------------------------------------------
# BT scan results — mix of audio and non-audio devices
# ---------------------------------------------------------------------------
DEMO_SCAN_RESULTS = [
    # Configured speakers
    {"mac": "AA:BB:CC:DD:EE:01", "name": "Living Room", "adapter": ""},
    {"mac": "AA:BB:CC:DD:EE:02", "name": "Kitchen", "adapter": ""},
    {"mac": "AA:BB:CC:DD:EE:03", "name": "Bedroom", "adapter": ""},
    {"mac": "AA:BB:CC:DD:EE:04", "name": "Garage", "adapter": ""},
    {"mac": "AA:BB:CC:DD:EE:05", "name": "Bathroom", "adapter": ""},
    # New audio devices (not yet configured)
    {"mac": "11:22:33:44:55:01", "name": "Patio", "adapter": ""},
    {"mac": "11:22:33:44:55:02", "name": "Office Headphones", "adapter": ""},
]

# ---------------------------------------------------------------------------
# Paired devices (returned by /api/bt/paired)
# ---------------------------------------------------------------------------
DEMO_PAIRED_DEVICES = [
    {"mac": "AA:BB:CC:DD:EE:01", "name": "Living Room", "connected": True},
    {"mac": "AA:BB:CC:DD:EE:02", "name": "Kitchen", "connected": True},
    {"mac": "AA:BB:CC:DD:EE:03", "name": "Bedroom", "connected": False},
    {"mac": "AA:BB:CC:DD:EE:04", "name": "Garage", "connected": True},
    {"mac": "AA:BB:CC:DD:EE:05", "name": "Bathroom", "connected": True},
    {"mac": "11:22:33:44:55:03", "name": "JBL Flip 5", "connected": False},
    {"mac": "11:22:33:44:55:04", "name": "Sony WH-1000XM4", "connected": False},
    {"mac": "11:22:33:44:55:05", "name": "Marshall Emberton", "connected": False},
]

# ---------------------------------------------------------------------------
# Fake adapter info
# ---------------------------------------------------------------------------
DEMO_ADAPTER_MAC = "00:1A:7D:DA:71:01"
DEMO_ADAPTER_INFO = {
    "address": DEMO_ADAPTER_MAC,
    "name": "Demo Adapter",
    "powered": True,
    "discoverable": False,
    "pairable": True,
}

# ---------------------------------------------------------------------------
# Music Assistant mock data
# ---------------------------------------------------------------------------

DEMO_MA_URL = "http://demo-ma.local:8095"
DEMO_MA_TOKEN = "demo-token-not-real"

# Syncgroups returned by discover_ma_groups
DEMO_MA_ALL_GROUPS = [
    {
        "id": "syncgroup_downstairs",
        "name": "Downstairs",
        "members": [
            {
                "id": "sendspin-demo-living-room",
                "name": "Living Room",
                "state": "playing",
                "volume": 75,
                "available": True,
            },
            {
                "id": "sendspin-demo-kitchen",
                "name": "Kitchen",
                "state": "idle",
                "volume": 50,
                "available": True,
            },
        ],
    },
    {
        "id": "syncgroup_garage",
        "name": "Garage",
        "members": [
            {
                "id": "sendspin-demo-garage",
                "name": "Garage",
                "state": "playing",
                "volume": 60,
                "available": True,
            },
        ],
    },
    {
        "id": "syncgroup_all",
        "name": "Whole House",
        "members": [
            {
                "id": "sendspin-demo-living-room",
                "name": "Living Room",
                "state": "playing",
                "volume": 75,
                "available": True,
            },
            {
                "id": "sendspin-demo-kitchen",
                "name": "Kitchen",
                "state": "idle",
                "volume": 50,
                "available": True,
            },
            {
                "id": "sendspin-demo-bedroom",
                "name": "Bedroom",
                "state": "idle",
                "volume": 100,
                "available": False,
            },
            {
                "id": "sendspin-demo-garage",
                "name": "Garage",
                "state": "playing",
                "volume": 60,
                "available": True,
            },
            {
                "id": "sendspin-demo-bathroom",
                "name": "Bathroom",
                "state": "idle",
                "volume": 35,
                "available": True,
            },
        ],
    },
]

# name_map returned by discover_ma_groups (player_name.lower → group info)
DEMO_MA_NAME_MAP: dict[str, dict] = {
    "living room": {"id": "syncgroup_downstairs", "name": "Downstairs"},
    "kitchen": {"id": "syncgroup_downstairs", "name": "Downstairs"},
    "bedroom": {"id": "syncgroup_all", "name": "Whole House"},
    "garage": {"id": "syncgroup_garage", "name": "Garage"},
    "bathroom": {"id": "syncgroup_all", "name": "Whole House"},
}

# Now-playing data per syncgroup
DEMO_MA_NOW_PLAYING = {
    "syncgroup_downstairs": {
        "connected": True,
        "state": "playing",
        "track": "Bohemian Rhapsody",
        "artist": "Queen",
        "album": "A Night at the Opera",
        "image_url": "",
        "elapsed": 120,
        "elapsed_updated_at": 0,  # filled at runtime
        "duration": 354,
        "shuffle": False,
        "repeat": "off",
        "queue_index": 0,
        "queue_total": 10,
        "syncgroup_id": "syncgroup_downstairs",
        "syncgroup_name": "Downstairs",
    },
    "syncgroup_garage": {
        "connected": True,
        "state": "playing",
        "track": "Hotel California",
        "artist": "Eagles",
        "album": "Hotel California",
        "image_url": "",
        "elapsed": 45,
        "elapsed_updated_at": 0,
        "duration": 391,
        "shuffle": False,
        "repeat": "off",
        "queue_index": 3,
        "queue_total": 10,
        "syncgroup_id": "syncgroup_garage",
        "syncgroup_name": "Garage",
    },
}

# Server info returned by MA discovery/validate
DEMO_MA_SERVER_INFO = {
    "url": DEMO_MA_URL,
    "version": "2.6.0",
    "server_id": "demo-server-id",
    "schema_version": 25,
    "onboard_done": True,
    "homeassistant_addon": True,
}

# Tracks for now-playing simulation (cycled by simulator)
DEMO_TRACKS = [
    ("Bohemian Rhapsody", "Queen", 354000),
    ("Hotel California", "Eagles", 391000),
    ("Stairway to Heaven", "Led Zeppelin", 482000),
    ("Imagine", "John Lennon", 187000),
    ("Smells Like Teen Spirit", "Nirvana", 301000),
    ("Billie Jean", "Michael Jackson", 294000),
    ("Sweet Child O' Mine", "Guns N' Roses", 356000),
    ("Like a Rolling Stone", "Bob Dylan", 369000),
    ("Hey Jude", "The Beatles", 431000),
    ("Lose Yourself", "Eminem", 326000),
]
