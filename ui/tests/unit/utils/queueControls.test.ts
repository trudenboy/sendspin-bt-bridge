import { describe, it, expect, vi, beforeEach } from 'vitest'
import { queueControls, nextRepeat, sendQueueCommand } from '@/utils/queueControls'
import { transport } from '@/api/playback'
import { queueCommand } from '@/api/ma'
import { makeDevice } from '../../fixtures/device'

vi.mock('@/api/playback', () => ({ transport: vi.fn().mockResolvedValue(undefined) }))
vi.mock('@/api/ma', () => ({ queueCommand: vi.fn().mockResolvedValue({}) }))

const NATIVE = ['play', 'pause', 'shuffle', 'unshuffle', 'repeat_off', 'repeat_one', 'repeat_all', 'seek']
const maQueue = (available: boolean) => ({
  actions: { queue_control: { supported: true, currently_available: available, blocked_reason: available ? null : 'No queue.' } },
})

describe('queueControls', () => {
  it('offers nothing without native commands or a Music Assistant queue', () => {
    const q = queueControls(makeDevice({ capabilities: maQueue(false) }))
    expect(q.shuffle.available).toBe(false)
    expect(q.repeat.available).toBe(false)
    expect(q.seek.available).toBe(false)
    expect(q.any).toBe(false)
  })

  it('uses the native Sendspin controller when the server offers the commands', () => {
    const q = queueControls(
      makeDevice({ playback: { supported_commands: NATIVE, track: { duration_ms: 200_000 } }, capabilities: maQueue(false) }),
    )
    expect(q.shuffle).toMatchObject({ available: true, native: true })
    expect(q.repeat).toMatchObject({ available: true, native: true })
    expect(q.seek).toMatchObject({ available: true, native: true })
  })

  it("falls back to Music Assistant's queue when the native controller lacks them", () => {
    const q = queueControls(
      makeDevice({
        playback: { supported_commands: ['play', 'pause'] },
        music_assistant: { now_playing: { duration: 180 } },
        capabilities: maQueue(true),
      }),
    )
    expect(q.shuffle).toMatchObject({ available: true, native: false })
    expect(q.repeat).toMatchObject({ available: true, native: false })
    expect(q.seek).toMatchObject({ available: true, native: false })
  })

  it('cannot seek without knowing the track length', () => {
    expect(queueControls(makeDevice({ playback: { supported_commands: NATIVE } })).seek.available).toBe(false)
  })

  it('offers nothing while Sendspin is disconnected', () => {
    const q = queueControls(makeDevice({ playback: { supported_commands: NATIVE, server_connected: false } }))
    expect(q.any).toBe(false)
  })

  it("reads shuffle and repeat from Music Assistant first, then the player's own state", () => {
    const own = queueControls(makeDevice({ playback: { shuffle: true, repeat_mode: 'one' } }))
    expect([own.shuffle.on, own.repeat.mode]).toEqual([true, 'one'])
    const ma = queueControls(
      makeDevice({ playback: { shuffle: true, repeat_mode: 'one' }, music_assistant: { now_playing: { shuffle: false, repeat: 'all' } } }),
    )
    expect([ma.shuffle.on, ma.repeat.mode]).toEqual([false, 'all'])
    const none = queueControls(makeDevice())
    expect([none.shuffle.on, none.repeat.mode]).toEqual([false, 'off'])
  })

  it('is pending while Music Assistant confirms a queue command', () => {
    const np = { _sync_meta: { pending_ops: [{ action: 'shuffle' }] } }
    expect(queueControls(makeDevice({ music_assistant: { now_playing: np } })).pending).toBe(true)
    expect(queueControls(makeDevice()).pending).toBe(false)
  })
})

describe('nextRepeat', () => {
  it('cycles off, all, one', () => {
    expect([nextRepeat('off'), nextRepeat('all'), nextRepeat('one')]).toEqual(['all', 'one', 'off'])
  })
})

describe('sendQueueCommand', () => {
  beforeEach(() => vi.clearAllMocks())

  const native = (extra = {}) =>
    makeDevice({ id: 'kitchen', playback: { supported_commands: NATIVE, track: { duration_ms: 200_000 }, ...extra } })
  const viaMa = (np = {}) =>
    makeDevice({
      id: 'kitchen',
      playback: { supported_commands: [], group: { id: 'g1' } },
      music_assistant: { syncgroup_id: 'sg1', now_playing: { duration: 180, ...np } },
      capabilities: maQueue(true),
    })

  it('toggles shuffle natively', async () => {
    await sendQueueCommand(native({ shuffle: true }), 'shuffle')
    expect(transport).toHaveBeenCalledWith('kitchen', 'unshuffle')
    await sendQueueCommand(native({ shuffle: false }), 'shuffle')
    expect(transport).toHaveBeenLastCalledWith('kitchen', 'shuffle')
  })

  it('cycles repeat natively', async () => {
    await sendQueueCommand(native({ repeat_mode: 'all' }), 'repeat')
    expect(transport).toHaveBeenCalledWith('kitchen', 'repeat_one')
  })

  it('seeks natively in ms', async () => {
    await sendQueueCommand(native(), 'seek', 61_000)
    expect(transport).toHaveBeenCalledWith('kitchen', 'seek', 61_000)
  })

  it("goes through Music Assistant's queue otherwise (seek in seconds)", async () => {
    const target = { device_id: 'kitchen', syncgroup_id: 'sg1', group_id: 'g1' }
    await sendQueueCommand(viaMa({ shuffle: false }), 'shuffle')
    expect(queueCommand).toHaveBeenCalledWith('shuffle', target, true)
    await sendQueueCommand(viaMa({ repeat: 'one' }), 'repeat')
    expect(queueCommand).toHaveBeenLastCalledWith('repeat', target, 'off')
    await sendQueueCommand(viaMa(), 'seek', 61_400)
    expect(queueCommand).toHaveBeenLastCalledWith('seek', target, 61)
    expect(transport).not.toHaveBeenCalled()
  })
})
