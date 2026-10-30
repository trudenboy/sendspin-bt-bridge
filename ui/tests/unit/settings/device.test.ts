import { describe, it, expect } from 'vitest'
import spec from '../../../openapi.json'
import en from '@/i18n/locales/en.json'
import ru from '@/i18n/locales/ru.json'
import { DEVICE_FIELDS, DEVICE_GROUPS, DEVICE_MANAGED_KEYS, deviceFieldFor, deviceFieldVisible } from '@/settings/device'

type Schema = Record<string, any>
const schemas = (spec as Schema).components.schemas as Record<string, Schema>

function resolve(s: Schema | undefined): Schema | undefined {
  if (!s) return undefined
  if (s.$ref) return resolve(schemas[s.$ref.split('/').pop()!])
  if (s.anyOf) return resolve(s.anyOf.find((b: Schema) => b.type !== 'null'))
  return s
}

const props = schemas.Player.properties as Record<string, Schema>
// Dotted keys (``backend.adapter``, ``backend.sink``) resolve through the backend schemas.
const fieldSchema = (key: string): Schema | undefined => {
  if (!key.startsWith('backend.')) return props[key]
  const name = key.slice('backend.'.length)
  return schemas.BluetoothBackend!.properties[name] ?? schemas.PulseSinkBackend!.properties[name]
}
const lookup = (m: Schema, k: string) => k.split('.').reduce<any>((o, p) => (o == null ? undefined : o[p]), m)

describe('device settings layout', () => {
  it('accounts for every Player key', () => {
    const covered = new Set([...DEVICE_FIELDS.map((f) => f.key), ...DEVICE_MANAGED_KEYS])
    expect(Object.keys(props).filter((k) => !covered.has(k))).toEqual([])
  })

  it.each(DEVICE_FIELDS.map((f) => [f.key, f] as const))('%s matches the API schema', (_k, field) => {
    const s = resolve(fieldSchema(field.key))!
    expect(s, field.key).toBeDefined()
    if (field.kind === 'number') {
      expect(['integer', 'number']).toContain(s.type)
      expect(field.min).toBe(s.minimum)
      expect(field.max).toBe(s.maximum)
    } else if (field.kind === 'select') {
      if (field.optionsFrom) expect(s.type).toBe('string')
      else expect(field.options).toEqual(s.enum)
    } else if (field.kind === 'toggle') expect(s.type).toBe('boolean')
    else expect(s.type).toBe('string')
  })

  it('shows the schema default for every key that has one', () => {
    const wrong = DEVICE_FIELDS.filter((f) => {
      const raw = fieldSchema(f.key)!
      if (!('default' in raw) || f.optionsFrom) return false
      return f.default !== raw.default
    }).map((f) => f.key)
    expect(wrong).toEqual([])
  })

  it.each(DEVICE_FIELDS.map((f) => [f.key, f] as const))('%s has text in en and ru', (_k, field) => {
    for (const m of [en, ru]) {
      expect(lookup(m, `settings.device.${field.key}.label`), field.key).toBeTypeOf('string')
      expect(lookup(m, `settings.device.${field.key}.help`), field.key).toBeTypeOf('string')
      for (const o of field.options ?? []) expect(lookup(m, `settings.device.${field.key}.options.${o}`)).toBeTypeOf('string')
    }
  })

  it('names every group in en and ru', () => {
    for (const g of DEVICE_GROUPS) for (const m of [en, ru]) expect(lookup(m, `settings.deviceGroups.${g.id}`)).toBeTypeOf('string')
  })

  describe('by backend', () => {
    const bt = { backend: { type: 'bluetooth_a2dp', mac: 'AA:BB', adapter: 'hci0' } }
    const pulse = { backend: { type: 'pulse_sink', sink: 'alsa_output.usb' } }
    const field = (key: string) => DEVICE_FIELDS.find((f) => f.key === key)!

    it('shows the adapter only for a Bluetooth speaker', () => {
      expect(deviceFieldVisible(field('backend.adapter'), bt)).toBe(true)
      expect(deviceFieldVisible(field('backend.adapter'), pulse)).toBe(false)
    })

    it('shows the output choice only for a local audio output', () => {
      expect(deviceFieldVisible(field('backend.sink'), pulse)).toBe(true)
      expect(deviceFieldVisible(field('backend.sink'), bt)).toBe(false)
    })

    it('keeps the idle-mode dependent fields working', () => {
      expect(deviceFieldVisible(field('keep_alive_method'), { ...pulse, idle_mode: 'keep_alive' })).toBe(true)
      expect(deviceFieldVisible(field('keep_alive_method'), pulse)).toBe(false)
    })

    it('offers no disconnect idle mode or infrasound for an output', () => {
      expect(deviceFieldFor(field('idle_mode'), pulse).options).toEqual(['default', 'power_save', 'keep_alive'])
      expect(deviceFieldFor(field('keep_alive_method'), pulse).options).toEqual(['silence', 'none'])
      expect(deviceFieldFor(field('idle_mode'), bt).options).toContain('auto_disconnect')
      expect(deviceFieldFor(field('keep_alive_method'), bt).options).toContain('infrasound')
    })

    it('defaults an output to the Sendspin volume controller and silent keep-alive', () => {
      expect(deviceFieldFor(field('volume_controller'), pulse).default).toBe('sendspin')
      expect(deviceFieldFor(field('keep_alive_method'), pulse).default).toBe('silence')
      expect(deviceFieldFor(field('volume_controller'), bt).default).toBe('pa')
    })
  })
})
