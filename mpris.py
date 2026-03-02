"""
MPRIS D-Bus integration for sendspin-bt-bridge.

Provides:
- MprisIdentityService: registers a minimal MediaPlayer2 D-Bus service so
  Music Assistant can discover this bridge by player name.
- _pause_all_via_mpris(): send Pause to all playing sendspin instances.
- _read_mpris_metadata_for(): read track/artist/playback state for a PID.
"""

import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional dbus import — gracefully degraded if not available
# ---------------------------------------------------------------------------

_DBUS_MPRIS_AVAILABLE = False
MprisIdentityService = None
_GLib = None
try:
    import dbus.service, dbus.mainloop.glib
    from gi.repository import GLib as _GLib

    class MprisIdentityService(dbus.service.Object):
        """Minimal MPRIS MediaPlayer2 service — exposes Identity = effective player name."""

        def __init__(self, player_name: str, index: int = 0):
            safe = ''.join(c if c.isalnum() else '' for c in player_name)[:32] or f'i{index}'
            bus_name = dbus.service.BusName(
                f'org.mpris.MediaPlayer2.SendspinBridge.{safe}',
                dbus.SessionBus()
            )
            super().__init__(bus_name, '/org/mpris/MediaPlayer2')
            self._identity = player_name

        @dbus.service.method('org.freedesktop.DBus.Properties',
                             in_signature='ss', out_signature='v')
        def Get(self, iface, prop):
            return self.GetAll(iface).get(prop, dbus.String(''))

        @dbus.service.method('org.freedesktop.DBus.Properties',
                             in_signature='s', out_signature='a{sv}')
        def GetAll(self, iface):
            if iface == 'org.mpris.MediaPlayer2':
                return {
                    'Identity':            dbus.String(self._identity),
                    'CanQuit':             dbus.Boolean(False),
                    'CanRaise':            dbus.Boolean(False),
                    'HasTrackList':        dbus.Boolean(False),
                    'DesktopEntry':        dbus.String('sendspin'),
                    'SupportedUriSchemes': dbus.Array([], signature='s'),
                    'SupportedMimeTypes':  dbus.Array([], signature='s'),
                }
            return {}

    _DBUS_MPRIS_AVAILABLE = True
except Exception:
    pass


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------

def pause_all_via_mpris() -> int:
    """Send MPRIS Pause to all playing sendspin instances on the session bus.

    Returns the number of players that were successfully paused.
    All D-Bus calls are synchronous — call via run_in_executor from async contexts.
    """
    paused = 0
    try:
        import dbus
        bus = dbus.SessionBus()
        for name in bus.list_names():
            sname = str(name)
            if not sname.startswith('org.mpris.MediaPlayer2.Sendspin'):
                continue
            if 'SendspinBridge' in sname:
                continue
            try:
                obj = bus.get_object(sname, '/org/mpris/MediaPlayer2')
                props = dbus.Interface(obj, 'org.freedesktop.DBus.Properties')
                pb = str(props.Get('org.mpris.MediaPlayer2.Player', 'PlaybackStatus'))
                if pb == 'Playing':
                    player = dbus.Interface(obj, 'org.mpris.MediaPlayer2.Player')
                    player.Pause()
                    logger.info(f"Sent MPRIS Pause to {sname}")
                    paused += 1
            except Exception as _e:
                logger.debug(f"MPRIS pause skipped for {sname}: {_e}")
    except Exception as _e:
        logger.debug(f"MPRIS pause unavailable: {_e}")
    return paused


def read_mpris_metadata_for(pid: int):
    """Read track/artist/playback-state from MPRIS for the given sendspin PID.

    Returns (artist, track, playback_status) where playback_status is
    'Playing', 'Paused', or 'Stopped'. All fields are None on failure.
    """
    try:
        import dbus  # optional dependency — may not be installed
        bus = dbus.SessionBus()
        service_name = f'org.mpris.MediaPlayer2.Sendspin.instance{pid}'
        try:
            obj = bus.get_object(service_name, '/org/mpris/MediaPlayer2')
            iface = dbus.Interface(obj, 'org.freedesktop.DBus.Properties')
            meta = iface.Get('org.mpris.MediaPlayer2.Player', 'Metadata')
            title = str(meta.get('xesam:title', '') or '')
            artists = meta.get('xesam:artist', [])
            artist = str(artists[0]) if artists else ''
            playback_status = str(iface.Get('org.mpris.MediaPlayer2.Player', 'PlaybackStatus'))
            return artist or None, title or None, playback_status
        except Exception:
            return None, None, None
    except Exception:
        return None, None, None
