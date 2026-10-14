import { transport } from '@/api/playback'
import { queueCommand } from '@/api/ma'
import type { Device } from '@/api/types'

export type RepeatMode = 'off' | 'all' | 'one'

export interface QueueControls {
  shuffle: { available: boolean; native: boolean; on: boolean }
  repeat: { available: boolean; native: boolean; mode: RepeatMode }
  seek: { available: boolean; native: boolean }
  /** Music Assistant has not confirmed a queue command yet. */
  pending: boolean
  /** Shuffle or repeat can be used: the queue controls are there. */
  any: boolean
}

interface NowPlaying {
  shuffle?: unknown
  repeat?: unknown
  duration?: unknown
  _sync_meta?: { pending_ops?: { action?: string }[] }
}

const QUEUE_ACTIONS = new Set(['next', 'previous', 'shuffle', 'repeat', 'seek'])
const REPEAT_CYCLE: RepeatMode[] = ['off', 'all', 'one']

const asRepeat = (value: unknown): RepeatMode | null =>
  value === 'off' || value === 'all' || value === 'one' ? value : null

/** Off → all → one → off, as stable's repeat button cycles. */
export function nextRepeat(mode: RepeatMode): RepeatMode {
  return REPEAT_CYCLE[(REPEAT_CYCLE.indexOf(mode) + 1) % REPEAT_CYCLE.length]!
}

const nowPlaying = (d: Device) => (d.music_assistant.now_playing ?? null) as NowPlaying | null

/**
 * Shuffle, repeat and seek for one player, as stable 2.76 offers them: the
 * native Sendspin controller when the server advertises the command (lower
 * latency), else Music Assistant's queue when it has one for the player.
 * State comes from Music Assistant first, then the player's own report.
 */
export function queueControls(d: Device): QueueControls {
  const commands = d.playback.supported_commands ?? []
  const has = (...names: string[]) => names.every((n) => commands.includes(n))
  const linked = d.playback.server_connected
  const actions = (d.capabilities as { actions?: Record<string, { currently_available?: boolean | null }> }).actions
  const maQueue = linked && actions?.queue_control?.currently_available === true
  const np = nowPlaying(d)

  const shuffleNative = linked && has('shuffle', 'unshuffle')
  const repeatNative = linked && has('repeat_off', 'repeat_all', 'repeat_one')
  const seekNative = linked && has('seek')
  const npDuration = typeof np?.duration === 'number' ? np.duration : 0
  const knowsLength = npDuration > 0 || (d.playback.track.duration_ms ?? 0) > 0

  const shuffleOn = typeof np?.shuffle === 'boolean' ? np.shuffle : (d.playback.shuffle ?? false)
  const mode = asRepeat(np?.repeat) ?? asRepeat(d.playback.repeat_mode) ?? 'off'
  const pending = (np?._sync_meta?.pending_ops ?? []).some((op) => QUEUE_ACTIONS.has(String(op?.action)))

  const shuffle = { available: shuffleNative || maQueue, native: shuffleNative, on: shuffleOn }
  const repeat = { available: repeatNative || maQueue, native: repeatNative, mode }
  return {
    shuffle,
    repeat,
    seek: { available: knowsLength && (seekNative || maQueue), native: seekNative },
    pending,
    any: shuffle.available || repeat.available,
  }
}

/** Toggle shuffle, step repeat, or seek to a position (ms), by the route queueControls picked. */
export function sendQueueCommand(d: Device, kind: 'shuffle' | 'repeat' | 'seek', positionMs?: number) {
  const q = queueControls(d)
  const target = {
    device_id: d.id,
    syncgroup_id: d.music_assistant.syncgroup_id ?? undefined,
    group_id: d.playback.group.id ?? undefined,
  }
  if (kind === 'shuffle') {
    if (q.shuffle.native) return transport(d.id, q.shuffle.on ? 'unshuffle' : 'shuffle')
    return queueCommand('shuffle', target, !q.shuffle.on)
  }
  if (kind === 'repeat') {
    const next = nextRepeat(q.repeat.mode)
    if (q.repeat.native) return transport(d.id, `repeat_${next}`)
    return queueCommand('repeat', target, next)
  }
  const ms = Math.max(0, Math.round(positionMs ?? 0))
  if (q.seek.native) return transport(d.id, 'seek', ms)
  return queueCommand('seek', target, Math.round(ms / 1000))
}
