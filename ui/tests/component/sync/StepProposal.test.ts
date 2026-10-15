import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import StepProposal from '@/components/sync/StepProposal.vue'
import en from '@/i18n/en.json'
const i18n = createI18n({ legacy: false, locale: 'en', messages: { en } })
const members = [{ ma_player_id: 'a', name: 'Kitchen' }, { ma_player_id: 'b', name: 'Hall' }, { ma_player_id: 'c', name: 'Attic' }]
const proposal = { reference: 'a', rows: [{ member: 'a', before_ms: 0, after_ms: 0, delta_ms: 0, clamped: false }, { member: 'b', before_ms: 100, after_ms: 5000, delta_ms: 4900, clamped: true }],
  predicted_spread_ms: 150, unverified: ['c'], warnings: ['Some delays were clamped to 5000 ms; predicted residual 150 ms.'] }

describe('StepProposal', () => {
  it('shows before → after, clamping, unverified members and applies', async () => {
    const w = mount(StepProposal, { global: { plugins: [i18n] }, props: { session: { id: 's1', mode: 'semi', state: 'proposed', members, history: [{ round: 1, spread_before_ms: 4900 }], error: null } as never, proposal: proposal as never, mode: 'semi' } })
    expect(w.find('[data-row="b"]').text()).toContain('100'); expect(w.find('[data-row="b"]').text()).toContain('5000')
    expect(w.find('[data-row="b"]').text()).toContain('+4900'); expect(w.find('[data-row="b"] [data-clamped]').exists()).toBe(true)
    expect(w.text()).toContain('Attic'); expect(w.text()).toContain('150 ms')
    await w.find('[data-apply]').trigger('click'); expect(w.emitted('apply')).toHaveLength(1)
  })
  it('offers the values to copy when Music Assistant refused the write', async () => {
    const write = vi.fn(); vi.stubGlobal('navigator', { clipboard: { writeText: write } })
    const w = mount(StepProposal, { global: { plugins: [i18n] }, props: { session: { id: 's1', mode: 'semi', state: 'proposed', members, history: [], error: 'ma_forbidden' } as never, proposal: proposal as never, mode: 'semi' } })
    expect(w.text()).toContain('administrator')
    await w.find('[data-copy-values]').trigger('click')
    expect(write).toHaveBeenCalledWith('Kitchen: 0 ms\nHall: 5000 ms')
  })
})

/* -- beyond the brief's two: warnings, reference, departed rows, save, check again ------- */

const base = { id: 's1', mode: 'semi', state: 'proposed', members, history: [{ round: 1, spread_before_ms: 4900 }], error: null, departed: [], reference: null }
const mountStep = (session: Record<string, unknown> = {}, prop: Record<string, unknown> = {}, mode = 'semi') =>
  mount(StepProposal, { global: { plugins: [i18n] }, attachTo: document.body, props: { session: { ...base, ...session } as never, proposal: { ...proposal, ...prop } as never, mode: mode as never } })

describe('StepProposal details', () => {
  it('warns about clamping with the predicted residual and about delays above 2000 ms', () => {
    const w = mountStep()
    const warnings = w.get('[data-warnings]').text()
    expect(warnings).toContain('clamped')
    expect(warnings).toContain('150 ms')
    expect(warnings).toContain('2000 ms')
  })

  it('has no warnings when nothing is clamped or large', () => {
    const w = mountStep({}, { rows: [{ member: 'a', before_ms: 0, after_ms: 0, delta_ms: 0, clamped: false }, { member: 'b', before_ms: 10, after_ms: 30, delta_ms: 20, clamped: false }], warnings: [], predicted_spread_ms: 0 })
    expect(w.find('[data-warnings]').exists()).toBe(false)
    expect(w.get('[data-row="b"]').text()).toContain('+20')
  })

  it('shows a negative change with a minus sign', () => {
    const w = mountStep({}, { rows: [{ member: 'b', before_ms: 50, after_ms: 30, delta_ms: -20, clamped: false }] })
    expect(w.get('[data-row="b"]').text()).toContain('−20')
  })

  it('draws the spread before and the predicted spread after', () => {
    const w = mountStep()
    const chart = w.get('[data-spread-chart]')
    expect(chart.get('[data-bar="before"]').text()).toContain('4900 ms')
    expect(chart.get('[data-bar="after"]').text()).toContain('150 ms')
    expect(chart.attributes('aria-label')).toContain('4900')
  })

  it('lets the operator choose the reference speaker (default: the planner’s)', async () => {
    const w = mountStep()
    const select = w.get<HTMLSelectElement>('[data-reference]')
    expect(select.element.value).toBe('a')
    await select.setValue('b')
    expect(w.emitted('reference')).toEqual([['b']])
  })

  it('applies without a member that left the group and marks its row', async () => {
    const w = mountStep({ departed: ['b'] })
    expect(w.get('[data-row="b"]').text()).toContain('Left the group')
    await w.get('[data-apply]').trigger('click')
    expect(w.emitted('apply')![0]).toEqual([{ a: 0 }, false])
  })

  it('asks to check again in semi-automatic mode only', async () => {
    const w = mountStep()
    await w.get('[data-check-again]').setValue(true)
    await w.get('[data-apply]').trigger('click')
    expect(w.emitted('apply')![0]).toEqual([undefined, true])
    expect(mountStep({}, {}, 'auto').find('[data-check-again]').exists()).toBe(false)
  })

  it('asks for a name before applying and saving as a profile', async () => {
    const w = mountStep()
    await w.get('[data-apply-save]').trigger('click')
    const input = document.querySelector<HTMLInputElement>('[data-profile-name]')!
    expect(input.value).not.toBe('')
    input.value = 'Evening'; input.dispatchEvent(new Event('input'))
    ;(document.querySelector('[data-save-confirm]') as HTMLButtonElement).click()
    await w.vm.$nextTick()
    expect(w.emitted('applyAndSave')).toEqual([['Evening', undefined, false]])
    w.unmount()
  })

  it('cancels', async () => {
    const w = mountStep()
    await w.get('[data-cancel]').trigger('click')
    expect(w.emitted('cancel')).toHaveLength(1)
  })
})
