"""Cross-integration identity helpers for HA's device_registry merge.

Music Assistant exposes each Sendspin BT speaker as
``media_player.<name>`` and registers a HA device with
``identifiers={("music_assistant", f"up{uuid_hex_no_dashes}")}`` —
``uuid`` being the same UUIDv5(MAC) the bridge uses as ``player_id``.
MA does NOT populate ``connections`` on its device, so the bridge's
``connections=[("bluetooth", mac)]`` block can't merge the cards via
the connections route.

Workaround: have the custom_component declare the SAME identifier MA
already owns (``("music_assistant", "up<hex>")``) alongside our own
``(DOMAIN, ...)`` identifier. HA's device_registry treats overlapping
identifiers as a merge signal across integrations, so the speaker
ends up under one device card with entities from both integrations.

Limitations
-----------
- Only works on the REST / custom_component path. The MQTT discovery
  surface hardcodes the identifier domain to ``mqtt`` and can't add
  a ``music_assistant``-domain identifier.
- Requires that the bridge's ``player_id`` is a canonical
  8-4-4-4-12 hex UUID (matches ``config._player_id_from_mac()``
  output for v2.6.1+ configs). Older configs that derived
  ``player_id`` from the player name fall back to ``None`` — the
  caller must skip the MA identifier in that case to avoid emitting
  a string that doesn't match anything in MA.
- Sensitive to MA's identifier scheme. If MA ever drops the ``up``
  prefix or moves to a different hash, this helper returns the wrong
  identifier and merge silently fails. The unit test uses a known-
  good fingerprint observed in production (issue #210 follow-up).
"""

from __future__ import annotations

import re

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def ma_identifier_for_player(player_id: str | None) -> str | None:
    """Return MA's ``device_info`` identifier value for *player_id*.

    Returns ``None`` for inputs that aren't a canonical UUID — the
    caller should treat that as "skip the MA identifier" rather than
    emit a malformed value that wouldn't match anything in MA.

    >>> ma_identifier_for_player("fcc3c5f3-15b2-5ddb-99d2-f64b915d8c25")
    'upfcc3c5f315b25ddb99d2f64b915d8c25'
    >>> ma_identifier_for_player("not-a-uuid") is None
    True
    >>> ma_identifier_for_player("") is None
    True
    >>> ma_identifier_for_player(None) is None
    True
    """
    if not isinstance(player_id, str):
        return None
    normalized = player_id.strip().lower()
    if not _UUID_RE.fullmatch(normalized):
        return None
    return "up" + normalized.replace("-", "")
