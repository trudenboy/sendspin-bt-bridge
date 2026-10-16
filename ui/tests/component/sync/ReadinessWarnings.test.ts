import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import ReadinessWarnings from '@/components/sync/ReadinessWarnings.vue'
import GroupMembers from '@/components/sync/GroupMembers.vue'
import en from '@/i18n/en.json'

const i18n = createI18n({ legacy: false, locale: 'en', messages: { en } })
const m = (id: string, over: Record<string, unknown> = {}) =>
  ({ ma_player_id: id, name: id.toUpperCase(), origin: 'sendspin', adjustable: true, delay_ms: 0, can_mute: true, available: true, volume: 40, muted: false, ...over })

describe('warnings before a measurement (UX audit M1, M2)', () => {
  it('names a speaker that cannot be muted and offers Manual tuning', async () => {
    const w = mount(ReadinessWarnings, { global: { plugins: [i18n] }, props: { members: [m('eneby'), m('kiosk', { name: 'Web Kiosk (wk_7) [10.0.0.9]', can_mute: false })] as never } })
    const issue = w.get('[data-issue="unmutable"]')
    expect(issue.text()).toContain('Web Kiosk cannot be muted. While it is in the group, the other speakers cannot be measured one at a time.')
    expect(issue.text()).not.toContain('10.0.0.9')
    await issue.get('[data-ready-manual]').trigger('click')
    expect(w.emitted('manual')).toHaveLength(1)
  })

  it('offers to skip an unavailable, a silent and a muted speaker', async () => {
    const w = mount(ReadinessWarnings, { global: { plugins: [i18n] }, props: { members: [
      m('a', { available: false }), m('b', { volume: 0 }), m('c', { muted: true }), m('d'),
    ] as never, skipped: ['b'] } })
    expect(w.findAll('[data-issue]').map((i) => i.attributes('data-issue'))).toEqual(['unavailable', 'silent', 'muted'])
    expect(w.get('[data-issue="silent"]').text()).toContain('B is at 0 %')
    await w.get('[data-skip-member="a"]').trigger('click')
    await w.get('[data-skip-member="b"]').trigger('click')
    expect(w.emitted('skip')).toEqual([['a', true], ['b', false]])
    expect(w.get('[data-skip-member="b"]').attributes('aria-pressed')).toBe('true')
  })

  it('says nothing when every speaker can be measured', () => {
    const w = mount(ReadinessWarnings, { global: { plugins: [i18n] }, props: { members: [m('a'), m('b')] as never } })
    expect(w.find('[data-readiness]').exists()).toBe(false)
  })

  it('flags the same things on the Group tab member list', () => {
    const w = mount(GroupMembers, { global: { plugins: [i18n] }, props: { members: [m('a', { can_mute: false, volume: 0 }), m('b', { available: false })] as never } })
    const [a, b] = w.findAll('[data-member]')
    expect(a!.findAll('[data-badge]').map((x) => x.text())).toEqual(['Cannot be muted', 'Volume 0 %'])
    expect(b!.get('[data-badge]').text()).toBe('Unavailable')
  })
})
