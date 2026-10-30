import { afterEach, describe, expect, it } from 'vitest'
import en from '@/i18n/locales/en.json'
import ru from '@/i18n/locales/ru.json'
import { i18n } from '@/i18n'
import { listNames, tAction, tProblem, tServer } from '@/i18n/server'
import keys from '../../fixtures/server-text-keys.json'

// The bridge writes texts with a stable code; the UI has words for every code the
// bridge can emit (fixture exported from the backend registry by
// scripts/export_server_text_keys.py) in both languages.

type Tree = { [k: string]: string | Tree }

function flat(tree: Tree, prefix = ''): Record<string, string> {
  return Object.entries(tree).reduce<Record<string, string>>((acc, [k, v]) => {
    if (typeof v === 'string') acc[prefix + k] = v
    else Object.assign(acc, flat(v, `${prefix}${k}.`))
    return acc
  }, {})
}

const E = flat(en as Tree)
const R = flat(ru as Tree)
const placeholders = (s: string) => [...new Set([...s.matchAll(/\{(\w+)\}/g)].map((m) => m[1]!))].sort()
const forms = (s: string) => s.split(' | ').length

describe('server text codes', () => {
  it('every code the bridge can emit has English and Russian words', () => {
    const missing = (keys as string[]).flatMap((k) => [E[k] ? [] : [`en ${k}`], R[k] ? [] : [`ru ${k}`]].flat())
    expect(missing).toEqual([])
  })

  it('the language files hold no server text the bridge cannot emit', () => {
    const known = new Set(keys as string[])
    expect(Object.keys(E).filter((k) => k.startsWith('server.') && !known.has(k))).toEqual([])
    expect(Object.keys(R).filter((k) => k.startsWith('server.') && !known.has(k))).toEqual([])
  })

  it('English and Russian interpolate the same values', () => {
    const bad = (keys as string[]).filter((k) => {
      const a = placeholders(E[k]!)
      const b = placeholders(R[k]!)
      // Russian may drop a value a language does not need, never add one.
      return !b.every((p) => a.includes(p))
    })
    expect(bad).toEqual([])
  })

  it('Russian has one form per English form, or one more (few / many)', () => {
    const bad = (keys as string[]).filter((k) => ![forms(E[k]!), forms(E[k]!) + 1].includes(forms(R[k]!)))
    expect(bad).toEqual([])
  })

  it('no text names a retired glossary term (the glossary lint covers server.* too)', () => {
    const hits = Object.entries(E).filter(([k, v]) => k.startsWith('server.') && /(?<!\{)\bsinks?\b|re-?anchor|hand(ed)? over/i.test(v))
    expect(hits).toEqual([])
  })
})

describe('tServer', () => {
  afterEach(() => {
    i18n.global.locale.value = 'en'
  })

  it('translates a code with its values and picks the plural form', () => {
    const one = { code: 'missing_sink', params: { count: 1, device: 'Kitchen', device_names: ['Kitchen'] } }
    expect(tServer('issue', one, 'title', 'fallback')).toBe('Kitchen is connected but has no audio output ready')
    const two = { code: 'missing_sink', params: { count: 2, device: 'A', device_names: ['A', 'B'] } }
    expect(tServer('issue', two, 'title', 'x')).toBe('2 speakers are connected but have no audio output ready')
    expect(tServer('issue', two, 'summary', 'x')).toBe('Reconnect or check the audio setup for A and B.')
  })

  it('uses Russian plural forms', () => {
    i18n.global.locale.value = 'ru'
    const title = (count: number) =>
      tServer('issue', { code: 'disconnected', params: { count, device: 'Кухня', device_names: ['Кухня'] } }, 'title', '')
    expect(title(1)).toBe('Кухня: колонка отключена')
    expect(title(2)).toBe('2 колонки отключены')
    expect(title(5)).toBe('5 колонок отключены')
  })

  it('shows the bridge English for an item without a code or with an unknown code', () => {
    expect(tServer('issue', {}, 'title', 'English only')).toBe('English only')
    expect(tServer('issue', { code: 'brand_new_code' }, 'title', 'English only')).toBe('English only')
    expect(tServer('issue', null, 'summary', 'x')).toBe('x')
  })

  it('picks the summary variant and falls back when the variant is unknown', () => {
    const base = { code: 'bluetooth_unavailable', params: {} }
    expect(tServer('issue', { ...base, summary_variant: 'daemon_down' }, 'summary', '')).toContain('bluetoothd')
    expect(tServer('issue', { ...base, summary_variant: 'nope' }, 'summary', 'English')).toBe('English')
    expect(tServer('issue', base, 'summary', '')).toContain('no Bluetooth adapter')
  })

  it('appends the reconnect attempt, with and without a limit', () => {
    const item = (params: Record<string, unknown>) => ({ code: 'disconnected', params: { count: 1, device: 'K', ...params } })
    expect(tServer('issue', item({ attempt: 2, max_attempts: 5 }), 'summary', '')).toContain('Reconnect attempt 2/5. 3 attempts remain')
    expect(tServer('issue', item({ attempt: 2, max_attempts: 0 }), 'summary', '')).toContain('Reconnect attempt 2 is in progress.')
  })

  it('expands cause codes into the words of the current language', () => {
    const item = { code: 'air_sensor_unavailable', params: { count: 1, device: 'K', causes_codes: ['cpu_budget', 'internal_error'] } }
    expect(tServer('issue', item, 'summary', '')).toContain('used more CPU than its budget')
    expect(tServer('issue', item, 'summary', '')).toContain('internal error')
    i18n.global.locale.value = 'ru'
    expect(tServer('issue', item, 'summary', '')).toContain('превысило лимит процессора')
  })

  it('formats long name lists', () => {
    expect(listNames(['A', 'B'])).toBe('A and B')
    expect(listNames(['A', 'B', 'C', 'D', 'E'])).toBe('A, B, and 3 more')
  })

  it('translates an action label with its count and value', () => {
    expect(tAction({ label: 'Reconnect 3 devices', code: 'reconnect_device', params: { count: 3 } })).toBe('Reconnect 3 speakers')
    expect(tAction({ label: 'Apply 600 ms latency', code: 'apply_latency_recommended', params: { count: 1, value: 600 } })).toBe(
      'Apply 600 ms latency',
    )
    expect(tAction({ label: 'Something new' })).toBe('Something new')
    i18n.global.locale.value = 'ru'
    expect(tAction({ label: 'x', code: 'release_bluetooth', params: { count: 1 } })).toBe('Освободить колонку')
    expect(tAction({ label: 'x', code: 'reclaim_bluetooth', params: { count: 1 } })).toBe('Вернуть колонку')
  })

  it('translates a timeline event and marks a recovered error', () => {
    const e = { code: 'runtime-error', params: { error: 'Route degraded', recovered: true } }
    expect(tServer('event', e, 'title', 'runtime-error')).toBe('Error')
    expect(tServer('event', e, 'summary', '')).toBe('Route degraded (recovered)')
    const refused = { code: 'ma-server-refused', params: { host: '10.0.0.2' } }
    expect(tServer('event', refused, 'summary', '')).toBe('Refused Music Assistant at 10.0.0.2: this bridge belongs to another server')
  })

  it('a header label from the lead issue is looked up in the issue area', () => {
    const header = { code: 'disconnected', area: 'issue', params: { count: 1, device: 'Kitchen' } }
    expect(tServer('header', header, 'title', '')).toBe('Kitchen is disconnected')
    expect(tServer('header', { code: 'startup', params: { percent: 40 } }, 'label', '')).toBe('Startup 40%')
  })

  it('shows our words for a fixed API problem and the bridge detail otherwise', () => {
    expect(tProblem({ code: 'unauthorized', detail: 'Not authenticated' })).toBe('Sign in to continue.')
    expect(tProblem({ code: 'invalid_adapter', detail: 'Invalid adapter identifier: x' })).toBe('Invalid adapter identifier: x')
    expect(tProblem({ code: 'weird', title: 'Weird' })).toBe('Weird')
  })
})
