import { describe, it, expect } from 'vitest'
import { confirmDialog, confirmState, settleConfirm } from '@/composables/useConfirm'

describe('confirmDialog', () => {
  it('opens with the question and resolves with the answer', async () => {
    const answer = confirmDialog({ title: 'Forget?', danger: true })
    expect(confirmState.open).toBe(true)
    expect(confirmState.options?.title).toBe('Forget?')
    settleConfirm(true)
    await expect(answer).resolves.toBe(true)
    expect(confirmState.open).toBe(false)
  })

  it('cancels a pending question when a new one is asked', async () => {
    const first = confirmDialog({ title: 'One' })
    const second = confirmDialog({ title: 'Two' })
    await expect(first).resolves.toBe(false)
    settleConfirm(false)
    await expect(second).resolves.toBe(false)
  })
})
