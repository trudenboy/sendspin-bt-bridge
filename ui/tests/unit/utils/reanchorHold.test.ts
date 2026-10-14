import { describe, it, expect, beforeEach } from 'vitest'
import { observeReanchor, resetReanchorMemory } from '@/utils/reanchorHold'
import { makeDevice } from '../../fixtures/device'

const dev = (timing: Record<string, unknown> = {}, extra: Record<string, unknown> = {}) =>
  makeDevice({ id: 'x', playback: { playing: true }, audio: { streaming: true }, timing: { available: true, ...timing }, ...extra })

describe('observeReanchor', () => {
  beforeEach(() => resetReanchorMemory())

  it('holds re-anchoring for 5 s after it clears', () => {
    expect(observeReanchor(dev({ reanchoring: true }), 1000)).toBeNull()
    expect(observeReanchor(dev({ reanchoring: false }), 2000)).toBe(7000)
    expect(observeReanchor(dev({ reanchoring: false }), 6999)).toBe(7000)
    expect(observeReanchor(dev({ reanchoring: false }), 7000)).toBeNull()
  })

  it('holds at least the static delay when that is longer', () => {
    observeReanchor(dev({ reanchoring: true }, { audio: { streaming: true, static_delay_ms: 8000 } }), 0)
    expect(observeReanchor(dev({ reanchoring: false }, { audio: { streaming: true, static_delay_ms: 8000 } }), 100)).toBe(8100)
  })

  it('holds when a re-anchor happened between two updates', () => {
    expect(observeReanchor(dev({ reanchor_count: 2, last_reanchor_at: 100 }), 0)).toBeNull()
    expect(observeReanchor(dev({ reanchor_count: 3, last_reanchor_at: 200 }), 1000)).toBe(6000)
  })

  it('notices a new re-anchor time even when the count restarted', () => {
    observeReanchor(dev({ reanchor_count: 4, last_reanchor_at: 100 }), 0)
    expect(observeReanchor(dev({ reanchor_count: 1, last_reanchor_at: 200 }), 10)).toBe(5010)
  })

  it('does not hold on first sight of an old count, nor once playback stops', () => {
    expect(observeReanchor(dev({ reanchor_count: 9, last_reanchor_at: 100 }), 0)).toBeNull()
    observeReanchor(dev({ reanchoring: true }), 0)
    expect(observeReanchor(dev({ reanchoring: false }, { playback: { playing: false } }), 10)).toBeNull()
  })
})
