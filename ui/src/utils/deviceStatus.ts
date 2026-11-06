import type { Device } from '@/api/types'
import { isPulseSink } from './deviceBackend'

/**
 * A player's status, derived in one place.
 *
 * The card's state badge, its Bluetooth chip, the list row, the drawer, the
 * filters, the sort order and the fleet summary all ask "is it connected?"
 * and "what state is it in?". For a Bluetooth speaker both answers follow the
 * bridge's ``links.bluetooth`` — the same fact the chip renders — so a badge
 * can never say Reconnecting beside a chip that says Standby, or Ready beside
 * Disconnected. A local audio output has no Bluetooth link: its sink being
 * present is what connected means.
 */

type LinkAware = Pick<Device, 'backend' | 'audio' | 'links'>

/** The Bluetooth link's state as the bridge derived it; null for a local output. */
function bluetoothLink(device: Pick<Device, 'links'>): string | null {
  return device.links?.bluetooth?.state ?? null
}

/** The output of a local audio output player is gone (unplugged or renamed away). */
export function isOutputMissing(device: Pick<Device, 'backend' | 'audio'>): boolean {
  return isPulseSink(device) && device.audio.sink_missing === true
}

/**
 * Whether the player can play right now: a Bluetooth speaker's link is
 * connected; a local audio output has its sink present.
 */
export function isDeviceConnected(device: LinkAware): boolean {
  if (isPulseSink(device)) return device.audio.has_sink === true && !device.audio.sink_missing
  return bluetoothLink(device) === 'connected'
}

/**
 * One status per speaker, most decisive first: switched off, handed over to
 * other devices, stopping, reconnecting, parked on standby, output gone,
 * muted at system level, playing, starting to play, then its health.
 */
export function deviceState(d: Device): string {
  if (!d.enabled) return 'disabled'
  const link = bluetoothLink(d)
  if (link === 'released') return 'released'
  if (d.sendspin.stopping) return 'stopping'
  if (link === 'connecting' || d.music_assistant.reconnecting) return 'reconnecting'
  if (link === 'standby') return 'standby'
  if (isOutputMissing(d)) return 'output_missing'
  const connected = isDeviceConnected(d)
  if (connected && d.audio.has_sink && d.audio.sink_muted && !d.audio.muted) return 'sink_muted'
  if (d.playback.playing && d.audio.streaming) return 'streaming'
  if (connected && isBuffering(d)) return 'buffering'
  // A local output has no Bluetooth link: present means connected, whatever an
  // older bridge's Bluetooth-only health said.
  if (isPulseSink(d) && d.health.state === 'offline') return connected ? 'ready' : 'offline'
  const health = d.health.state
  // Health may explain a problem; it may not call a player whose link is down ready.
  if (health && health !== 'unknown' && (connected || !CONNECTED_HEALTH.has(health))) return health
  return connected ? 'ready' : 'offline'
}

/** Health states that claim the player can play. */
const CONNECTED_HEALTH = new Set(['ready', 'streaming'])

/** Audio is buffering, or Music Assistant plays to the speaker but it has not started yet. */
function isBuffering(d: Device): boolean {
  if (d.audio.buffering) return true
  const np = d.music_assistant.now_playing as { state?: unknown } | null
  return np?.state === 'playing' && d.playback.server_connected && d.audio.has_sink && !d.playback.playing
}
