"""Constants for the Sendspin BT Bridge custom integration."""

from __future__ import annotations

DOMAIN = "sendspin_bridge"

# config_flow / config entry keys
CONF_HOST = "host"
CONF_PORT = "port"
CONF_TOKEN = "token"
CONF_BRIDGE_ID = "bridge_id"
CONF_BRIDGE_NAME = "bridge_name"
CONF_USE_HTTPS = "use_https"

# Update coordinator
DEFAULT_PORT = 8080
DEFAULT_SCAN_INTERVAL_SECS = 30
DEFAULT_RECONNECT_BACKOFF_SECS = 5
DEFAULT_RECONNECT_BACKOFF_MAX_SECS = 60

# REST endpoints (relative to host:port)
ENDPOINT_HA_STATE = "/api/ha/state"
ENDPOINT_STATUS_EVENTS = "/api/status/events"
ENDPOINT_HA_PAIR = "/api/auth/ha-pair"

# Per-device entity unique-id prefix
UNIQUE_ID_PREFIX = "sendspin"
