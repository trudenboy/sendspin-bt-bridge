import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

/**
 * The source-mode toggle runs for seconds on real hardware (the HFP switch, the
 * A2DP restore). The store owns the per-device transition the card shows while
 * it runs, so the queue and the reconciliation stay the real ones; only the
 * HTTP surface and the event stream are faked.
 */
vi.mock('@/composables/useEventStream', () => ({
  DEFAULT_EVENT_TYPES: ['status', 'job'],
  useEventStream: () => ({ connected: { value: false }, connect: vi.fn(), disconnect: vi.fn() }),
}))
vi.mock('@/api/status', () => ({ getStatus: vi.fn(), restartBridge: vi.fn() }))
vi.mock('@/api/source', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/source')>()),
  selectSource: vi.fn(),
  clearActiveSource: vi.fn(),
}))

import { ApiError } from '@/api/client'
import { getStatus } from '@/api/status'
import { clearActiveSource, selectSource } from '@/api/source'
import { useSourceStore } from '@/stores/source'

const MAC = 'AA:BB:CC:DD:EE:FF'
const INPUT_ID = `headset-mic:${MAC}`
const OTHER_MAC = '11:22:33:44:55:66'

const bridgeStatus = () => ({ bridge: { sendspin_source: null }, devices: [], groups: [] }) as never

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

let store: ReturnType<typeof useSourceStore>

describe('useSourceStore: source-mode transitions', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    vi.mocked(getStatus).mockResolvedValue(bridgeStatus())
    vi.mocked(selectSource).mockResolvedValue({ active_input: INPUT_ID } as never)
    vi.mocked(clearActiveSource).mockResolvedValue(undefined as never)
    store = useSourceStore()
  })

  afterEach(() => {
    store.teardown()
  })

  it('marks the owning device as switching to source while the command runs', async () => {
    const pending = deferred<{ active_input: string }>()
    vi.mocked(selectSource).mockReturnValue(pending.promise as never)

    const command = store.selectInput(INPUT_ID)
    await flushPromises()

    expect(store.transitionFor(MAC)).toBe('to-source')
    // Another device's card is not switching.
    expect(store.transitionFor(OTHER_MAC)).toBeNull()

    pending.resolve({ active_input: INPUT_ID })
    await command

    expect(store.transitionFor(MAC)).toBeNull()
  })

  it('marks the owning device as back to playback while the release runs', async () => {
    const pending = deferred<void>()
    vi.mocked(clearActiveSource).mockReturnValue(pending.promise as never)

    const command = store.release(MAC)
    await flushPromises()

    expect(store.transitionFor(MAC)).toBe('to-playback')
    expect(store.transitionFor(OTHER_MAC)).toBeNull()

    pending.resolve()
    await command

    expect(store.transitionFor(MAC)).toBeNull()
  })

  it('clears the transition but keeps the refusal for the card whose command failed', async () => {
    const pending = deferred<{ active_input: string }>()
    vi.mocked(selectSource).mockReturnValue(pending.promise as never)

    const command = store.selectInput(INPUT_ID)
    await flushPromises()
    expect(store.transitionFor(MAC)).toBe('to-source')

    pending.reject(
      new ApiError({
        status: 409,
        code: 'headset_profile_unavailable',
        detail: 'Could not switch the headset to HFP.',
      }),
    )
    await expect(command).rejects.toMatchObject({ code: 'headset_profile_unavailable' })

    expect(store.transitionFor(MAC)).toBeNull()
    expect(store.failureFor(MAC)).toMatchObject({ code: 'headset_profile_unavailable' })
    // The refusal belongs to the card that attempted the switch.
    expect(store.failureFor(OTHER_MAC)).toBeNull()
  })

  it('a new attempt clears the refusal from the card', async () => {
    vi.mocked(selectSource).mockRejectedValueOnce(
      new ApiError({ status: 409, code: 'headset_profile_unavailable', detail: 'No HFP.' }),
    )
    await expect(store.selectInput(INPUT_ID)).rejects.toMatchObject({ code: 'headset_profile_unavailable' })
    expect(store.failureFor(MAC)).not.toBeNull()

    await store.selectInput(INPUT_ID)

    expect(store.failureFor(MAC)).toBeNull()
  })
})
