import type { Device, DeviceLinks, LinkStatus } from '@/api/types'
import base from './device-base.json'

type DeepPartial<T> = { [K in keyof T]?: T[K] extends object ? DeepPartial<T[K]> : T[K] }

function merge<T>(target: T, patch: DeepPartial<T>): T {
  const out = structuredClone(target) as Record<string, unknown>
  for (const [key, value] of Object.entries(patch as Record<string, unknown>)) {
    const current = out[key]
    out[key] =
      value && typeof value === 'object' && !Array.isArray(value) && current && typeof current === 'object'
        ? merge(current, value as DeepPartial<typeof current>)
        : value
  }
  return out as T
}

const link = (state: string, tone: string, reason: string | null = null): LinkStatus => ({ state, tone, reason })

/**
 * The links the bridge derives from a device's facts, so a fixture that says
 * ``bluetooth.connected: false`` also reads as disconnected on its chip.
 * Mirrors ``bluetooth_connection_state`` and ``audio_link_state`` in
 * ``services/diagnostics/status_model.py``; a test that wants a particular
 * link passes ``links`` itself.
 */
function linksOf(d: Device): DeviceLinks {
  let bluetooth: LinkStatus | null = null
  if (d.backend?.type !== 'pulse_sink') {
    const bt = d.bluetooth
    if (!d.enabled) bluetooth = link('disabled', 'neutral')
    else if (bt?.management_enabled === false)
      bluetooth = bt.released_by === 'auto' ? link('released', 'warning', 'auto') : link('released', 'neutral', 'user')
    else if (bt?.reconnecting) bluetooth = link('connecting', 'warning', 'reconnecting')
    else if (bt?.standby) bluetooth = link('standby', 'neutral')
    else if (bt?.connected) bluetooth = link('connected', 'success')
    else if (bt?.never_paired) bluetooth = link('not_paired', 'error')
    else bluetooth = link('disconnected', 'error')
  }
  const bts = bluetooth?.state ?? null
  let audio: LinkStatus
  if (!d.enabled) audio = link('unavailable', 'neutral', 'disabled')
  else if (bts === 'released' || bts === 'standby') audio = link('unavailable', 'neutral', bts)
  else if (d.playback.server_connected)
    audio = d.playback.playing && d.audio.streaming ? link('streaming', 'success') : link('connected', 'success')
  else if (bts !== null && bts !== 'connected') audio = link('unavailable', 'neutral', 'bluetooth_down')
  else audio = link('disconnected', 'warning')
  return { audio_link: audio, bluetooth }
}

/** A complete API v1 device (as the bridge serializes it), with overrides. */
export function makeDevice(overrides: DeepPartial<Device> = {}): Device {
  const device = merge(base as unknown as Device, overrides)
  if (overrides.links === undefined) device.links = linksOf(device)
  else if (overrides.links.bluetooth === undefined) device.links.bluetooth = linksOf(device).bluetooth
  return device
}
