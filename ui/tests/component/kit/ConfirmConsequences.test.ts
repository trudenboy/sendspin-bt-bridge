import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import ConfirmConsequences from '@/kit/ConfirmConsequences.vue'

const mountIt = (props: Record<string, unknown> = {}) =>
  mount(ConfirmConsequences, {
    props: { modelValue: true, title: 'Remove Kitchen?', consequences: ['Playback stops on Kitchen', 'It leaves Music Assistant'], confirmLabel: 'Remove', cancelLabel: 'Cancel', danger: true, ...props },
    attachTo: document.body,
    global: { stubs: { teleport: true } },
  })

describe('ConfirmConsequences', () => {
  it('names each consequence as a list item and labels the destructive button', () => {
    const w = mountIt()
    const items = w.findAll('[data-testid="consequence"]').map((i) => i.text())
    expect(items).toEqual(['Playback stops on Kitchen', 'It leaves Music Assistant'])
    expect(w.find('[data-testid="confirm-yes"]').text()).toBe('Remove')
  })

  it('emits confirm and cancel', async () => {
    const w = mountIt()
    await w.find('[data-testid="confirm-yes"]').trigger('click')
    expect(w.emitted('confirm')).toHaveLength(1)
    await w.find('[data-testid="confirm-no"]').trigger('click')
    expect(w.emitted('cancel')).toHaveLength(1)
  })

  it('offers a side link such as "Download current config first"', async () => {
    let ran = 0
    const w = mountIt({ link: { label: 'Download current config first', run: () => { ran++ } } })
    await w.find('[data-testid="confirm-link"]').trigger('click')
    expect(ran).toBe(1)
  })
})
