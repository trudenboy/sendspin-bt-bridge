import { describe, it, expect } from 'vitest'
import spec from '../../../openapi.json'
import en from '@/i18n/en.json'
import ru from '@/i18n/ru.json'
import { ALL_FIELDS, COMPONENT_OWNED_KEYS, MANAGED_KEYS, SECTIONS, type FieldDef } from '@/settings/layout'

type Schema = Record<string, any>
const schemas = (spec as Schema).components.schemas as Record<string, Schema>

/** The non-null branch of ``anyOf: [X, null]``, with ``$ref`` resolved. */
function resolve(s: Schema | undefined): Schema | undefined {
  if (!s) return undefined
  if (s.$ref) return resolve(schemas[s.$ref.split('/').pop()!])
  if (s.anyOf) return resolve(s.anyOf.find((b: Schema) => b.type !== 'null'))
  return s
}

function propertySchema(path: string): Schema | undefined {
  let cur: Schema | undefined = schemas.BridgeConfig
  for (const part of path.split('.')) cur = resolve(resolve(cur)?.properties?.[part])
  return cur
}

function lookup(messages: Schema, key: string): unknown {
  return key.split('.').reduce<any>((o, k) => (o == null ? undefined : o[k]), messages)
}

const fieldKey = (f: FieldDef) => `settings.fields.${f.key}`

describe('settings layout', () => {
  it('accounts for every BridgeConfig key', () => {
    const covered = new Set([
      ...ALL_FIELDS.map((f) => f.key.split('.')[0]),
      ...Object.keys(COMPONENT_OWNED_KEYS),
      ...MANAGED_KEYS.map((k) => k.split('.')[0]),
    ])
    const missing = Object.keys(schemas.BridgeConfig.properties).filter((k) => !covered.has(k))
    expect(missing).toEqual([])
  })

  it('accounts for every HA_INTEGRATION sub-setting', () => {
    const covered = new Set([...ALL_FIELDS.map((f) => f.key), ...MANAGED_KEYS])
    const missing: string[] = []
    const ha = resolve(schemas.BridgeConfig.properties.HA_INTEGRATION)!
    for (const [k, v] of Object.entries<Schema>(ha.properties)) {
      const nested = resolve(v)
      if (nested?.properties) {
        for (const sub of Object.keys(nested.properties)) {
          if (!covered.has(`HA_INTEGRATION.${k}.${sub}`)) missing.push(`HA_INTEGRATION.${k}.${sub}`)
        }
      } else if (!covered.has(`HA_INTEGRATION.${k}`)) missing.push(`HA_INTEGRATION.${k}`)
    }
    expect(missing).toEqual([])
  })

  it('only names keys that exist, each once', () => {
    const keys = ALL_FIELDS.map((f) => f.key)
    expect(keys.filter((k) => !propertySchema(k))).toEqual([])
    expect(new Set(keys).size).toBe(keys.length)
  })

  it.each(ALL_FIELDS.map((f) => [f.key, f] as const))('%s matches the API schema', (_key, field) => {
    const s = propertySchema(field.key)!
    switch (field.kind) {
      case 'toggle':
        expect(s.type).toBe('boolean')
        break
      case 'number':
        expect(['integer', 'number']).toContain(s.type)
        expect(field.min).toBe(s.minimum)
        expect(field.max).toBe(s.maximum)
        break
      case 'select':
        expect(field.options).toEqual(s.enum)
        break
      case 'text':
      case 'password':
        expect(s.type).toBe('string')
        break
      case 'list':
        expect(s.type).toBe('array')
        expect(resolve(s.items)?.type).toBe('string')
        break
    }
  })

  it.each(ALL_FIELDS.map((f) => [f.key, f] as const))('%s has a label and help in en and ru', (_key, field) => {
    for (const messages of [en, ru]) {
      expect(lookup(messages, `${fieldKey(field)}.label`), `label ${field.key}`).toBeTypeOf('string')
      expect(lookup(messages, `${fieldKey(field)}.help`), `help ${field.key}`).toBeTypeOf('string')
    }
  })

  it('names and describes every section in en and ru', () => {
    for (const section of SECTIONS) {
      for (const messages of [en, ru]) {
        expect(lookup(messages, `settings.sections.${section.id}.title`), section.id).toBeTypeOf('string')
        expect(lookup(messages, `settings.sections.${section.id}.description`), section.id).toBeTypeOf('string')
      }
    }
  })
})
