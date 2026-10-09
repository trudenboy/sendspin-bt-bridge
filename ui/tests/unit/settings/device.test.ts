import { describe, it, expect } from 'vitest'
import spec from '../../../openapi.json'
import en from '@/i18n/en.json'
import ru from '@/i18n/ru.json'
import { DEVICE_FIELDS, DEVICE_GROUPS, DEVICE_MANAGED_KEYS } from '@/settings/device'

type Schema = Record<string, any>
const schemas = (spec as Schema).components.schemas as Record<string, Schema>

function resolve(s: Schema | undefined): Schema | undefined {
  if (!s) return undefined
  if (s.$ref) return resolve(schemas[s.$ref.split('/').pop()!])
  if (s.anyOf) return resolve(s.anyOf.find((b: Schema) => b.type !== 'null'))
  return s
}

const props = schemas.BluetoothDevice.properties as Record<string, Schema>
const lookup = (m: Schema, k: string) => k.split('.').reduce<any>((o, p) => (o == null ? undefined : o[p]), m)

describe('device settings layout', () => {
  it('accounts for every BluetoothDevice key', () => {
    const covered = new Set([...DEVICE_FIELDS.map((f) => f.key), ...DEVICE_MANAGED_KEYS])
    expect(Object.keys(props).filter((k) => !covered.has(k))).toEqual([])
  })

  it.each(DEVICE_FIELDS.map((f) => [f.key, f] as const))('%s matches the API schema', (_k, field) => {
    const s = resolve(props[field.key])!
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
      const raw = props[f.key]!
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
})
