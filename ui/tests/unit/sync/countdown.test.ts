import { describe, it, expect, vi } from 'vitest'
import { defineComponent, nextTick, ref } from 'vue'
import { mount } from '@vue/test-utils'
import { useCountdown } from '@/sync/countdown'

describe('useCountdown', () => {
  it('ticks down between readings, takes each new reading and stops at null', async () => {
    vi.useFakeTimers()
    const reading = ref<number | null>(100)
    let left!: ReturnType<typeof useCountdown>
    mount(defineComponent({ setup() { left = useCountdown(() => reading.value); return () => null } }))
    expect(left.value).toBe(100)
    vi.advanceTimersByTime(3000)
    expect(left.value).toBeCloseTo(97, 0)
    reading.value = 50; await nextTick()
    expect(left.value).toBe(50)
    reading.value = null; await nextTick()
    expect(left.value).toBeNull()
    vi.useRealTimers()
  })
})
