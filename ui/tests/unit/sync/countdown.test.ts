import { describe, it, expect, vi, afterEach } from 'vitest'
import { defineComponent, h, ref } from 'vue'
import { mount } from '@vue/test-utils'
import { useCountdown } from '@/components/sync/useCountdown'

function host(initial: number | null) {
  const seconds = ref<number | null>(initial)
  let left: ReturnType<typeof useCountdown> | null = null
  const w = mount(defineComponent({ setup() { left = useCountdown(() => seconds.value); return () => h('div') } }))
  return { w, seconds, left: () => left!.value }
}

describe('useCountdown', () => {
  afterEach(() => { vi.useRealTimers() })

  it('counts the seconds the bridge reported down, once a second, and stops at 0', () => {
    vi.useFakeTimers()
    const { left } = host(3.2)
    expect(left()).toBe(4)
    vi.advanceTimersByTime(1000); expect(left()).toBe(3)
    vi.advanceTimersByTime(5000); expect(left()).toBe(0)
  })

  it('follows a new report and clears when there is nothing to count', async () => {
    vi.useFakeTimers()
    const { seconds, left, w } = host(null)
    expect(left()).toBeNull()
    seconds.value = 10; await w.vm.$nextTick(); expect(left()).toBe(10)
    seconds.value = null; await w.vm.$nextTick(); expect(left()).toBeNull()
    w.unmount()
  })
})
