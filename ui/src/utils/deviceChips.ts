/**
 * What a device card and list row show about a speaker beyond its state:
 * sync, signal, group, adapter or output, blocked actions and handoff
 * readiness. Pure functions over the API's device, so cards, rows and tests
 * read the same answers. Thresholds and wording follow stable 2.76's
 * dashboard.
 */
import type { AudioSink, Device, Group } from '@/api/types'
import { isDeviceConnected, isPulseSink, resolveAdapter } from './deviceBackend'
import { deviceState } from '@/stores/devices'
import { safeHttpUrl } from './safeUrl'
import { queueControls } from './queueControls'

export type Tone = 'success' | 'warning' | 'error' | 'info' | 'neutral'

/* ---------------------------------------------------------------- sync -- */

export interface SyncBadge {
  kind: 'sync' | 'reanchoring' | 'playout' | 'warming'
  tone: Tone
  pulse: boolean
  /** |sync error| (or the last correction while re-anchoring), rounded ms. */
  errorMs: number | null
  /** warming: seconds until the stream counts as warm. */
  warmInS?: number | null
  count: number
  countTone: Tone
  count5m: number | null
  count30m: number | null
}

/**
 * Shown while the speaker streams and the daemon reports timing. ``held``
 * keeps "Re-anchoring" a moment after the daemon clears it (see reanchorHold).
 */
export function syncBadge(d: Device, held = false): SyncBadge | null {
  if (!(d.playback.playing && d.audio.streaming) || !d.timing.available) return null
  const timing = d.timing
  const count = timing.reanchor_count ?? 0
  const countTone: Tone = count > 100 ? 'error' : count > 10 ? 'warning' : count > 0 ? 'success' : 'neutral'
  const round = (v: number | null | undefined) => (v == null ? null : Math.round(Math.abs(v)))
  const playout = timing.playout
  if (playout && playout.mode !== 'off' && playout.state !== 'fallback') {
    const jumps = playout.jumps ?? 0
    const jc = { count: jumps, countTone: (jumps > 10 ? 'warning' : jumps > 0 ? 'success' : 'neutral') as Tone, count5m: null, count30m: null }
    if (!playout.warm && playout.warm_s != null && playout.warmup_s != null) {
      return {
        kind: 'warming', tone: 'neutral', pulse: true, errorMs: null,
        warmInS: Math.max(0, Math.ceil(playout.warmup_s - playout.warm_s)), ...jc,
      }
    }
    const err = playout.error_ms == null ? null : Math.round(Math.abs(playout.error_ms) * 10) / 10
    const tone: Tone = err == null ? 'neutral' : err <= 1 ? 'success' : err <= 5 ? 'warning' : 'error'
    return { kind: 'playout', tone, pulse: false, errorMs: err, ...jc }
  }
  const common = { count, countTone, count5m: timing.reanchor_count_5m, count30m: timing.reanchor_count_30m }
  if (timing.reanchoring || held) {
    return {
      kind: 'reanchoring',
      tone: 'warning',
      pulse: true,
      errorMs: round(timing.last_sync_error_ms ?? timing.sync_error_ms),
      ...common,
    }
  }
  return { kind: 'sync', tone: 'success', pulse: false, errorMs: round(timing.sync_error_ms), ...common }
}

/* -------------------------------------------------------------- signal -- */

export type SignalTier = 'excellent' | 'strong' | 'fair' | 'poor' | 'bad' | 'stale'

export interface SignalInfo {
  bars: 0 | 1 | 2 | 3 | 4
  tone: Tone
  tier: SignalTier
  /** The Δ dB offset from the controller's Golden Receive Power Range. */
  value: number
  stale: boolean
}

/** A reading older than this is greyed (stable: 6× the bridge's 5 s refresh). */
export const SIGNAL_STALE_MS = 30_000

/**
 * A connected BR/EDR link's RSSI is a Δ dB offset from the controller's
 * Golden Receive Power Range (0 = in range), greyed once its sample
 * (``rssi_at``) is older than SIGNAL_STALE_MS. The age is taken on the
 * bridge's clock when the speaker reports a timing sample (both times come
 * from the bridge, so a browser clock that runs ahead cannot grey it), else
 * against ``now``.
 */
export function signalInfo(d: Device, now: number = Date.now()): SignalInfo | null {
  const bt = d.bluetooth
  if (!bt || !bt.connected || bt.rssi_dbm == null || Number.isNaN(Number(bt.rssi_dbm))) return null
  const value = Number(bt.rssi_dbm)
  const sampledAt = bt.rssi_at ? Date.parse(bt.rssi_at) : Number.NaN
  const bridgeNow = d.timing.available && d.timing.sampled_at ? Date.parse(d.timing.sampled_at) : Number.NaN
  const reference = Number.isNaN(bridgeNow) ? now : bridgeNow
  if (!Number.isNaN(sampledAt) && reference - sampledAt > SIGNAL_STALE_MS) {
    return { value, stale: true, bars: 0, tone: 'neutral', tier: 'stale' }
  }
  const base = { value, stale: false }
  if (value >= 0) return { ...base, bars: 4, tone: 'success', tier: 'excellent' }
  if (value >= -10) return { ...base, bars: 3, tone: 'success', tier: 'strong' }
  if (value >= -20) return { ...base, bars: 2, tone: 'warning', tier: 'fair' }
  if (value >= -25) return { ...base, bars: 1, tone: 'warning', tier: 'poor' }
  return { ...base, bars: 1, tone: 'error', tier: 'bad' }
}

/* --------------------------------------------------------------- group -- */

export interface GroupChip {
  /** The group id as the device reports it (the tooltip shows it in full). */
  id: string
  label: string
  /** Members of the group on other bridges or player providers. */
  external: number
  /** Music Assistant's settings page for the group, when it is an MA sync group and MA's address is known. */
  url: string | null
}

/** Music Assistant's group settings page (stable's link), only for an http(s) base. */
export function maGroupUrl(base: string | null | undefined, groupId: string): string | null {
  const safe = safeHttpUrl(base)
  if (!safe) return null
  return `${safe.replace(/\/+$/, '')}/#/settings/editplayer/${encodeURIComponent(groupId)}`
}

/**
 * The speaker's Music Assistant group. A Sendspin session group of one is not
 * worth a chip; an MA sync group, a shared group or one with members
 * elsewhere is.
 */
export function groupChip(d: Device, groups: Group[], devices: Device[], maWebUrl: string | null): GroupChip | null {
  const syncgroupId = d.music_assistant.syncgroup_id
  const id = d.playback.group.id || syncgroupId
  if (!id) return null
  const summary = groups.find((g) => g.id === id)
  const external = summary?.external_count ?? 0
  const peers = devices.filter((o) => o.id !== d.id && o.playback.group.id === id).length
  if (!syncgroupId && !peers && !external) return null
  const name = d.playback.group.name || summary?.name
  const maId = syncgroupId || (id.startsWith('syncgroup_') ? id : null)
  return {
    id,
    label: name || `#${id.slice(-4)}`,
    external,
    url: maId ? maGroupUrl(maWebUrl, maId) : null,
  }
}

/* ---------------------------------------------------- adapter / output -- */

export type LinkState = 'connected' | 'disconnected' | 'reconnecting' | 'released' | 'autoReleased' | 'disabled'

export type LinkChip =
  | { kind: 'adapter'; label: string; hci: string; mac: string | null; tone: Tone; pulse: boolean; state: LinkState }
  | { kind: 'output'; label: string; sink: string }

/**
 * Where the speaker plays through: a Bluetooth adapter (coloured by the link,
 * as stable does) or, for a local audio output, the output itself.
 */
export function linkChip(d: Device, sinks: AudioSink[] = []): LinkChip | null {
  if (isPulseSink(d)) {
    const sink = d.audio.sink_name || d.backend.sink
    if (!sink) return null
    const description = sinks.find((s) => s.name === sink)?.description
    return { kind: 'output', label: description || sink, sink }
  }
  const bt = d.bluetooth
  const hci = bt?.adapter.hci || d.backend.adapter || ''
  if (!bt || !hci) return null
  const base = { kind: 'adapter' as const, label: bt.adapter.name || hci, hci, mac: bt.adapter.mac }
  if (!d.enabled) return { ...base, tone: 'neutral', pulse: false, state: 'disabled' }
  if (bt.management_enabled === false) {
    return bt.released_by === 'auto'
      ? { ...base, tone: 'warning', pulse: false, state: 'autoReleased' }
      : { ...base, tone: 'neutral', pulse: false, state: 'released' }
  }
  if (bt.reconnecting) return { ...base, tone: 'warning', pulse: true, state: 'reconnecting' }
  if (bt.connected) return { ...base, tone: 'success', pulse: false, state: 'connected' }
  return { ...base, tone: 'error', pulse: false, state: 'disconnected' }
}

/**
 * A disabled player's adapter (neutral: no link to colour) or output, from
 * the bridge's disabled list, which names the adapter by hci or MAC.
 */
export function disabledLinkChip(
  p: { output: boolean; adapter: string | null; sink: string | null },
  adapters: { id: string; mac: string; name: string }[] = [],
  sinks: AudioSink[] = [],
): LinkChip | null {
  if (p.output) {
    if (!p.sink) return null
    return { kind: 'output', label: sinks.find((s) => s.name === p.sink)?.description || p.sink, sink: p.sink }
  }
  if (!p.adapter) return null
  const { adapter, hci } = resolveAdapter(p.adapter, adapters)
  return {
    kind: 'adapter',
    label: adapter?.name || hci || p.adapter,
    hci,
    mac: adapter?.mac ?? null,
    tone: 'neutral',
    pulse: false,
    state: 'disabled',
  }
}

/* -------------------------------------------------------------- server -- */

export interface ServerChip {
  /** The server's name, else its address. */
  label: string
  name: string | null
  host: string | null
  id: string | null
  tone: Tone
  /** The bridge knows its own Music Assistant and this is another one. */
  foreign: boolean
}

/** The Music Assistant a connected player is on; a warning when it is not the bridge's own. */
export function serverChip(d: Device): ServerChip | null {
  if (!d.playback.server_connected) return null
  const { name, host, id, own } = d.playback.server
  const label = name || host
  if (!label) return null
  const foreign = own === false
  return { label, name: name ?? null, host: host ?? null, id: id ?? null, tone: foreign ? 'warning' : 'neutral', foreign }
}

/* ------------------------------------------------------ blocked hints -- */

export interface BlockedHint {
  /** The capability action key, or ``sink_muted``. */
  key: string
  /** The bridge's reason (English, from the capability payload); empty for sink_muted. */
  message: string
  dependsOn: string[]
  recommendedAction: string | null
}

interface Capability {
  supported?: boolean
  currently_available?: boolean | null
  blocked_reason?: string | null
  depends_on?: string[]
  recommended_action?: string | null
  blocked_reason_detail?: { depends_on?: string[] } | null
}

/** The actions stable explains on a card, in its order. */
const HINT_ACTIONS = ['reconnect', 'toggle_bt_management', 'play_pause', 'volume', 'queue_control'] as const
const BLUETOOTH_ONLY = new Set(['reconnect', 'toggle_bt_management'])

/** States whose badge already explains why controls are unavailable. */
const SELF_EXPLAINED = new Set(['offline', 'standby', 'released', 'disabled', 'output_missing'])

/**
 * Up to two controls the speaker cannot use right now, each with the bridge's
 * reason. Nothing when the state badge already says why (handed over,
 * standby, offline, output missing, disabled), and queue controls only while
 * the player streams without them: an idle player has no queue to control.
 */
export function blockedHints(d: Device, limit = 2): BlockedHint[] {
  if (SELF_EXPLAINED.has(deviceState(d))) return []
  const streaming = d.playback.playing && d.audio.streaming
  const hints: BlockedHint[] = []
  if (d.audio.sink_muted && !d.audio.muted && d.audio.has_sink) {
    hints.push({ key: 'sink_muted', message: '', dependsOn: [], recommendedAction: null })
  }
  const actions = ((d.capabilities as { actions?: Record<string, Capability> }).actions ?? {}) as Record<string, Capability>
  for (const key of HINT_ACTIONS) {
    if (isPulseSink(d) && BLUETOOTH_ONLY.has(key)) continue
    // Only while streaming, and only when the card has no shuffle/repeat to show.
    if (key === 'queue_control' && (!streaming || queueControls(d).any)) continue
    const cap = actions[key]
    if (!cap || cap.supported === false || cap.currently_available !== false || !cap.blocked_reason) continue
    hints.push({
      key,
      message: cap.blocked_reason,
      dependsOn: cap.depends_on?.length ? cap.depends_on : (cap.blocked_reason_detail?.depends_on ?? []),
      recommendedAction: cap.recommended_action || null,
    })
  }
  return hints.slice(0, limit)
}

/* ----------------------------------------------------------- transfer -- */

export interface TransferChip {
  ready: boolean
  tone: Tone
  reason: string
}

const SEVERITY_TONE: Record<string, Tone> = { error: 'error', warning: 'warning', info: 'info' }

/**
 * Room-handoff readiness, only when it is news: the player is connected (link
 * and output up) yet not ready. Ready players and those whose state badge
 * already says offline, standby, released, disabled or missing get no chip.
 */
export function transferChip(d: Device): TransferChip | null {
  const r = d.transfer_readiness as { ready?: unknown; reason?: unknown; severity?: unknown } | null
  if (!r || typeof r !== 'object' || r.ready === true) return null
  if (!isDeviceConnected(d) || !d.audio.has_sink || SELF_EXPLAINED.has(deviceState(d))) return null
  const ready = false
  const reason = String(r.reason ?? '')
  return { ready, tone: ready ? 'success' : (SEVERITY_TONE[String(r.severity)] ?? 'warning'), reason }
}
