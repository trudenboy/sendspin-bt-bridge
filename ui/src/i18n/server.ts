/**
 * Words for the texts the bridge writes (guidance, recovery cards, header pill,
 * action labels, timeline events, blocked reasons, API problems).
 *
 * The bridge sends a stable `code` and `params` next to its English text; the
 * wording lives in en.json / ru.json under `server.<area>.<code>.<leaf>`. A code
 * the files do not know shows the bridge's English text, so a newer bridge never
 * shows an empty line. Registry of codes: docs/ux-codes.md.
 */
import { i18n } from './index'

export interface ServerText {
  code?: string | null
  params?: Record<string, unknown> | null
  /** Picks `summary_<variant>` instead of `summary`. */
  summary_variant?: string | null
  /** Where the code is looked up when it is not the caller's default area. */
  area?: string | null
}

type Params = Record<string, unknown>

function exists(key: string): boolean {
  return i18n.global.te(key, i18n.global.locale.value) || i18n.global.te(key, 'en')
}

function say(key: string, params: Params = {}, plural?: number): string {
  const t = i18n.global.t as unknown as (key: string, named: Params, plural?: number) => string
  return plural === undefined ? t(key, params) : t(key, params, plural)
}

/** "A", "A and B", "A, B and C", then "A, B, and N more". */
export function listNames(names: string[]): string {
  if (names.length <= 3) {
    try {
      return new Intl.ListFormat(i18n.global.locale.value, { style: 'long', type: 'conjunction' }).format(names)
    } catch {
      return names.join(', ')
    }
  }
  return say('server.shared.list.more', { first: names[0], second: names[1], count: names.length - 2 })
}

/** The values a sentence can use: given params, plus `devices`, lists and cause texts. */
function paramsOf(item: ServerText): Params {
  const out: Params = { count: 1, ...(item.params ?? {}) }
  for (const [name, value] of Object.entries(out)) {
    if (Array.isArray(value)) {
      if (name.endsWith('_codes')) {
        const base = name.slice(0, -'_codes'.length)
        out[base] = value
          .map((c) => (exists(`server.cause.${c}.text`) ? say(`server.cause.${c}.text`) : String(c)))
          .join(' ')
      } else {
        out[name] = listNames(value.map(String))
      }
    } else if (name.endsWith('_code') && typeof value === 'string') {
      const base = name.slice(0, -'_code'.length)
      out[base] = exists(`server.cause.${value}.text`) ? say(`server.cause.${value}.text`) : value
    }
  }
  if (out.devices === undefined && out.device_names !== undefined) out.devices = out.device_names
  if (out.device === undefined) out.device = ''
  // A refusal without an owner, and a server's name shown in brackets only when it has one.
  if (!out.owner) out.owner = say('server.shared.another_server.text')
  if (typeof out.name === 'string' && out.name) out.name = `(${out.name})`
  return out
}

/**
 * The text of one leaf (`title`, `summary`, `label`, `message`, `detail`, `name`)
 * of a coded item, in the UI language; `fallback` (the bridge's English) when the
 * item has no code or the files have no words for it.
 */
export function tServer(area: string, item: ServerText | null | undefined, leaf: string, fallback = ''): string {
  const code = item?.code
  if (!item || !code) return fallback
  const where = item.area || area
  const variant = leaf === 'summary' && item.summary_variant ? `summary_${item.summary_variant}` : leaf
  const key = `server.${where}.${code}.${variant}`
  if (!exists(key)) return fallback
  const params = paramsOf(item)
  let text = say(key, params, typeof params.count === 'number' ? params.count : 1)
  if (leaf === 'summary' && params.recovered === true) text += ` ${say('server.shared.recovered.text')}`
  if (leaf === 'summary') {
    const attempt = params.attempt
    if (typeof attempt === 'number') {
      const max = typeof params.max_attempts === 'number' ? params.max_attempts : 0
      text += ` ${max > 0 ? say('server.shared.attempt.with_limit', { attempt, max, remaining: Math.max(max - attempt, 0) }) : say('server.shared.attempt.open', { attempt })}`
    }
  }
  return text
}

/** An action's label: `{key, label, code, params}` from a guidance or recovery item. */
export function tAction(action: { label: string; code?: string | null; params?: Params | null }): string {
  return tServer('action', action, 'label', action.label)
}

interface ProblemLike {
  code?: string | null
  detail?: string | null
  title?: string | null
}

/** An API problem for people: our words for the fixed ones, the bridge's `detail` otherwise. */
export function tProblem(problem: ProblemLike): string {
  const own = tServer('problem', { code: problem.code, params: {} }, 'detail', '')
  return own || problem.detail || problem.title || problem.code || ''
}
