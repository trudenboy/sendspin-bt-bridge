import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import GroupMembers from '@/components/sync/GroupMembers.vue'
import en from '@/i18n/en.json'

const i18n = createI18n({ legacy: false, locale: 'en', messages: { en } })

describe('GroupMembers', () => {
  it('shows origin, adjustability and the current delay', () => {
    const w = mount(GroupMembers, { global: { plugins: [i18n] }, props: { members: [
      { ma_player_id: 'a', name: 'Kitchen', origin: 'bridge', adjustable: true, delay_ms: 120, last_measured_at: null },
      { ma_player_id: 'cast', name: 'TV', origin: 'other', adjustable: false, delay_ms: null, last_measured_at: null },
    ] } })
    const rows = w.findAll('[data-member]')
    expect(rows[0]!.text()).toContain('Kitchen'); expect(rows[0]!.text()).toContain('Bridge'); expect(rows[0]!.text()).toContain('120 ms')
    expect(rows[1]!.text()).toContain('Cannot be adjusted'); expect(rows[1]!.text()).not.toContain('ms')
  })

  it('labels Sendspin players, a missing delay and the age of the last measurement', () => {
    const fiveMinutesAgo = new Date(Date.now() - 5 * 60_000).toISOString()
    const w = mount(GroupMembers, { global: { plugins: [i18n] }, props: { members: [
      { ma_player_id: 'b', name: 'Hall', origin: 'sendspin', adjustable: true, delay_ms: null, last_measured_at: fiveMinutesAgo },
    ] as never } })
    const row = w.get('[data-member]')
    expect(row.text()).toContain('Sendspin')
    expect(row.text()).toContain('—')
    expect(row.text()).toContain('5 minutes ago')
  })

  it('says when there are no members', () => {
    const w = mount(GroupMembers, { global: { plugins: [i18n] }, props: { members: [] } })
    expect(w.text()).toContain('This group has no members.')
  })
})
