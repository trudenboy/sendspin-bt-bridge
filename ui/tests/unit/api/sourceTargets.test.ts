import { describe, it, expect, vi, beforeEach } from 'vitest'

const get = vi.hoisted(() => vi.fn())
vi.mock('@/api/client', () => ({
  api: () => ({ GET: get }),
  unwrap: async (value: unknown) => value,
}))

import { clearSourceTargetCache, getSourceTargets, peekSourceTargets } from '@/api/source'

const answer = {
  targets: [{ player_id: 'kitchen', name: 'Kitchen', type: 'player', available: true }],
  selected: null,
  ma_api_available: true,
  error: null,
}

describe('source target cache', () => {
  beforeEach(() => {
    clearSourceTargetCache()
    get.mockReset()
    get.mockResolvedValue(answer)
  })

  it('serves a second open from the cache instead of asking MA again', async () => {
    const first = await getSourceTargets()
    const second = await getSourceTargets()

    expect(first).toEqual(answer)
    expect(second).toEqual(answer)
    expect(get).toHaveBeenCalledTimes(1)
    expect(peekSourceTargets()).toEqual(answer)
  })

  it('does not cache an unreachable MA, so the next open retries', async () => {
    get.mockResolvedValue({ ...answer, targets: [], error: 'ma_api_unreachable' })

    await getSourceTargets()
    await getSourceTargets()

    expect(get).toHaveBeenCalledTimes(2)
  })

  it('drops the cache on request', async () => {
    await getSourceTargets()
    clearSourceTargetCache()
    await getSourceTargets()

    expect(get).toHaveBeenCalledTimes(2)
  })
})
