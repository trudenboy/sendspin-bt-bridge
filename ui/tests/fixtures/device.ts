import type { Device } from '@/api/types'
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

/** A complete API v1 device (as the bridge serializes it), with overrides. */
export function makeDevice(overrides: DeepPartial<Device> = {}): Device {
  return merge(base as unknown as Device, overrides)
}
