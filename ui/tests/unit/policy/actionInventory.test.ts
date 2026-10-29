import { describe, it, expect } from 'vitest'
import { ACTION_POLICY } from '@/composables/useActionPolicy'

/**
 * Inventory of the action policy (decision Q3): reversible actions run at once
 * with an undo toast, disruptive or irreversible ones ask first with a dialog
 * that names the consequences. Every action id is registered in ACTION_POLICY;
 * a new action cannot skip the policy without failing here.
 */
const sources = import.meta.glob('/src/**/*.{vue,ts}', { query: '?raw', import: 'default', eager: true }) as Record<string, string>
const files = Object.entries(sources).filter(([path]) => !path.endsWith('.d.ts'))

const POLICY_FILES = ['/src/composables/useActionPolicy.ts', '/src/composables/useConfirm.ts']
const ids = (re: RegExp) =>
  files.flatMap(([path, text]) => [...text.matchAll(re)].map((m) => ({ path, id: m[1]! })))

describe('action policy inventory', () => {
  it('no component opens a raw confirm dialog; the policy is the only way in', () => {
    const offenders = files
      .filter(([path, text]) => !POLICY_FILES.includes(path) && !path.endsWith('ConfirmHost.vue') && /\bconfirmDialog\(/.test(text))
      .map(([path]) => path)
    expect(offenders).toEqual([])
  })

  it('every confirmAction id is registered as confirm or choice', () => {
    const used = ids(/\bconfirmAction\(\s*'([^']+)'/g)
    expect(used.length).toBeGreaterThan(10)
    for (const { path, id } of used) {
      expect(['confirm', 'choice'], `${id} in ${path}`).toContain(ACTION_POLICY[id as keyof typeof ACTION_POLICY])
    }
  })

  it('every undoable id is registered as undo', () => {
    const used = ids(/\bundoable\(\s*'([^']+)'/g)
    expect(used.length).toBeGreaterThan(5)
    for (const { path, id } of used) {
      expect(ACTION_POLICY[id as keyof typeof ACTION_POLICY], `${id} in ${path}`).toBe('undo')
    }
  })

  it('every registered action is used somewhere (no dead entries)', () => {
    const text = files.filter(([p]) => !POLICY_FILES.includes(p) && !p.includes('/tests/')).map(([, t]) => t).join('\n')
    for (const id of Object.keys(ACTION_POLICY)) {
      expect(text.includes(`'${id}'`), id).toBe(true)
    }
  })

  it('a destructive marker (red button or item) only appears in a file that asks first', () => {
    const marked = files.filter(
      ([path, text]) =>
        !path.startsWith('/src/kit/') &&
        (/:destructive="true"|\bdestructive\b(?=[\s>])/.test(text) || /variant="danger"/.test(text)),
    )
    expect(marked.length).toBeGreaterThan(0)
    for (const [path, text] of marked) {
      expect(/confirmAction\(|actions\.remove\(|useDisabledPlayerActions\(/.test(text), path).toBe(true)
    }
  })

  it('danger: true is set by the policy alone', () => {
    const offenders = files.filter(([p, t]) => !POLICY_FILES.includes(p) && /\bdanger:\s*true/.test(t)).map(([p]) => p)
    expect(offenders).toEqual([])
  })
})
