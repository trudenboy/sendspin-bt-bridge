import { describe, it, expect } from 'vitest'
import {
  BACKEND_REGISTRY,
  getBackendDescriptor,
  getBackendIcon,
  getBackendColor,
  type BackendType,
} from '../../../src/types/backend-registry'

describe('BACKEND_REGISTRY', () => {
  const expectedTypes: BackendType[] = [
    'bluetooth_a2dp',
    'local_sink',
    'usb_audio',
    'virtual_sink',
    'snapcast_client',
    'vban',
    'le_audio',
  ]

  it('contains all expected backend types', () => {
    const keys = Object.keys(BACKEND_REGISTRY)
    for (const type of expectedTypes) {
      expect(keys).toContain(type)
    }
  })

  it.each(expectedTypes)('%s has required fields', (type) => {
    const desc = BACKEND_REGISTRY[type]
    expect(desc.type).toBe(type)
    expect(desc.labelKey).toBeTruthy()
    expect(desc.icon).toBeDefined()
    expect(desc.color).toBeTruthy()
    expect(Array.isArray(desc.configFields)).toBe(true)
    expect(Array.isArray(desc.statusFields)).toBe(true)
    expect(Array.isArray(desc.signalPath)).toBe(true)
    expect(desc.signalPath.length).toBeGreaterThan(0)
  })

  it.each(expectedTypes)('%s configFields have required keys', (type) => {
    for (const field of BACKEND_REGISTRY[type].configFields) {
      expect(field.key).toBeTruthy()
      expect(field.labelKey).toBeTruthy()
      expect(['text', 'number', 'toggle', 'select', 'slider']).toContain(field.type)
    }
  })

  it.each(expectedTypes)('%s statusFields have required keys', (type) => {
    for (const field of BACKEND_REGISTRY[type].statusFields) {
      expect(field.key).toBeTruthy()
      expect(field.labelKey).toBeTruthy()
    }
  })
})

describe('getBackendDescriptor', () => {
  it('returns correct descriptor for known type', () => {
    const desc = getBackendDescriptor('bluetooth_a2dp')
    expect(desc.type).toBe('bluetooth_a2dp')
    expect(desc.labelKey).toBe('device.backend.bluetooth_a2dp')
  })

  it('falls back to bluetooth_a2dp for unknown type', () => {
    const desc = getBackendDescriptor('nonexistent_backend')
    expect(desc.type).toBe('bluetooth_a2dp')
  })
})

describe('getBackendIcon', () => {
  it('returns a component for each type', () => {
    expect(getBackendIcon('bluetooth_a2dp')).toBeDefined()
    expect(getBackendIcon('snapcast_client')).toBeDefined()
  })
})

describe('getBackendColor', () => {
  it('returns a color string', () => {
    expect(getBackendColor('bluetooth_a2dp')).toBe('primary')
    expect(getBackendColor('local_sink')).toBe('success')
  })
})
