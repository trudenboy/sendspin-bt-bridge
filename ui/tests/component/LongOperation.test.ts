import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import LongOperation from '@/components/LongOperation.vue'
import en from '@/i18n/en.json'
import ru from '@/i18n/ru.json'

const mountOp = (props: Record<string, unknown> = {}, locale = 'en') =>
  mount(LongOperation, {
    props: { label: 'Scanning…', ...props },
    slots: {},
    global: { plugins: [createI18n({ legacy: false, locale, messages: { en, ru } })] },
  })

describe('LongOperation', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('says what runs and counts the seconds', async () => {
    const w = mountOp({ startedAt: Date.now() })
    expect(w.get('[role="status"]').text()).toContain('Scanning…')
    vi.advanceTimersByTime(4000)
    await w.vm.$nextTick()
    expect(w.get('[data-testid="longop-elapsed"]').text()).toBe('4s')
  })

  it('shows a progress bar only when progress is known', () => {
    expect(mountOp().find('[role="progressbar"]').exists()).toBe(false)
    const bar = mountOp({ progress: 0.4 }).get('[role="progressbar"]')
    expect(bar.attributes('aria-valuenow')).toBe('40')
  })

  it('offers Cancel only where the backend supports it, and says it is cancelling', async () => {
    expect(mountOp().find('[data-testid="longop-cancel"]').exists()).toBe(false)
    const w = mountOp({ cancellable: true })
    await w.get('[data-testid="longop-cancel"]').trigger('click')
    expect(w.emitted('cancel')).toHaveLength(1)
    await w.setProps({ cancelling: true })
    expect(w.get('[data-testid="longop-cancel"]').attributes('disabled')).toBeDefined()
    expect(w.text()).toContain(en.longOp.cancelling)
  })

  it('on error shows the reason and the next step, with a way to try again', async () => {
    const w = mountOp({ status: 'error', retryable: true, error: 'Adapter busy', nextStep: 'Wait a minute and scan again.' })
    expect(w.get('[role="alert"]').text()).toContain('Adapter busy')
    expect(w.text()).toContain('Wait a minute and scan again.')
    expect(w.find('[data-testid="longop-elapsed"]').exists()).toBe(false)
    await w.get('[data-testid="longop-retry"]').trigger('click')
    expect(w.emitted('retry')).toHaveLength(1)
  })

  it('speaks Russian', () => {
    expect(mountOp({ cancellable: true }, 'ru').get('[data-testid="longop-cancel"]').text()).toBe(ru.longOp.cancel)
  })
})
