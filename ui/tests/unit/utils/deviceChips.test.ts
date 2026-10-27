import { describe, it, expect } from 'vitest'
import {
  blockedHints,
  groupChip,
  linkChip,
  maGroupUrl,
  serverChip,
  signalInfo,
  syncBadge,
  transferChip,
} from '@/utils/deviceChips'
import type { Group } from '@/api/types'
import { makeDevice } from '../../fixtures/device'

const streaming = (timing: Record<string, unknown> = {}) =>
  makeDevice({ playback: { playing: true }, audio: { streaming: true }, timing: { available: true, ...timing } })

const output = (extra: Record<string, unknown> = {}) =>
  makeDevice({
    backend: { type: 'pulse_sink', sink: 'alsa_output.usb', mac: null, adapter: null },
    bluetooth: null as never,
    audio: { sink_name: 'alsa_output.usb', has_sink: true, sink_missing: false },
    ...extra,
  })

describe('syncBadge', () => {
  it('is hidden while not streaming or when timing is unavailable', () => {
    expect(syncBadge(makeDevice({ timing: { available: true } }))).toBeNull()
    expect(syncBadge(streaming({ available: false }))).toBeNull()
  })

  it('says Sync with the absolute sync error when known', () => {
    const badge = syncBadge(streaming({ sync_error_ms: -3.6 }))!
    expect(badge.kind).toBe('sync')
    expect(badge.tone).toBe('success')
    expect(badge.pulse).toBe(false)
    expect(badge.errorMs).toBe(4)
    expect(syncBadge(streaming())!.errorMs).toBeNull()
  })

  it('says Re-anchoring (warning, pulsing) with the last correction', () => {
    const badge = syncBadge(streaming({ reanchoring: true, last_sync_error_ms: 41.2 }))!
    expect(badge.kind).toBe('reanchoring')
    expect(badge.tone).toBe('warning')
    expect(badge.pulse).toBe(true)
    expect(badge.errorMs).toBe(41)
  })

  it('carries the re-anchor counts and colours the count as stable does', () => {
    const badge = syncBadge(streaming({ reanchor_count: 12, reanchor_count_5m: 2, reanchor_count_30m: 7 }))!
    expect(badge.count).toBe(12)
    expect(badge.countTone).toBe('warning')
    expect(badge.count5m).toBe(2)
    expect(badge.count30m).toBe(7)
    expect(syncBadge(streaming({ reanchor_count: 3 }))!.countTone).toBe('success')
    expect(syncBadge(streaming({ reanchor_count: 101 }))!.countTone).toBe('error')
  })
  describe('playout', () => {
    const playout = (extra: Record<string, unknown> = {}) =>
      streaming({
        sync_error_ms: -3.6,
        playout: { mode: 'measure', state: 'playing', warm: true, warm_s: 90, warmup_s: 60, error_ms: 0.4, jumps: 2, ...extra },
      })

    it('shows the playout error with one decimal and tones it by size in loop mode (1 / 5 ms)', () => {
      expect(syncBadge(playout({ mode: 'loop' }))).toMatchObject({ kind: 'playout', tone: 'success', errorMs: 0.4, count: 2 })
      expect(syncBadge(playout({ mode: 'loop', error_ms: -3.24 }))).toMatchObject({ kind: 'playout', tone: 'warning', errorMs: 3.2 })
      expect(syncBadge(playout({ mode: 'loop', error_ms: 12 }))).toMatchObject({ kind: 'playout', tone: 'error', errorMs: 12 })
    })

    it('tones the error for measure noise in measure mode (5 / 20 ms)', () => {
      expect(syncBadge(playout({ error_ms: -3.24 }))).toMatchObject({ kind: 'playout', tone: 'success', errorMs: 3.2 })
      expect(syncBadge(playout({ error_ms: 5 }))).toMatchObject({ tone: 'success' })
      expect(syncBadge(playout({ error_ms: 12 }))).toMatchObject({ tone: 'warning', errorMs: 12 })
      expect(syncBadge(playout({ error_ms: 20 }))).toMatchObject({ tone: 'warning' })
      expect(syncBadge(playout({ error_ms: -20.5 }))).toMatchObject({ tone: 'error', errorMs: 20.5 })
    })

    it('tones the jump count at the 10 / 11 boundary', () => {
      expect(syncBadge(playout({ jumps: 10 }))).toMatchObject({ count: 10, countTone: 'success' })
      expect(syncBadge(playout({ jumps: 11 }))).toMatchObject({ count: 11, countTone: 'warning' })
    })

    it('says warming with the seconds left until the stream is warm', () => {
      expect(syncBadge(playout({ warm: false, warm_s: 25 }))).toMatchObject({ kind: 'warming', tone: 'neutral', warmInS: 35 })
    })

    it('keeps the legacy badge when mode is off, state is fallback or playout is null', () => {
      for (const p of [{ mode: 'off' }, { state: 'fallback', fallback_reason: 'no_sink' }]) {
        expect(syncBadge(playout(p))).toMatchObject({ kind: 'sync', errorMs: 4 })
      }
      expect(syncBadge(streaming({ sync_error_ms: -3.6, playout: null }))).toMatchObject({ kind: 'sync', errorMs: 4 })
    })
  })
})

describe('signalInfo', () => {
  const rssi = (rssi_dbm: number | null, connected = true) => makeDevice({ bluetooth: { rssi_dbm, connected } })

  it('maps the Δ dB offset to bars and tone as stable does', () => {
    expect(signalInfo(rssi(2))).toMatchObject({ bars: 4, tone: 'success', tier: 'excellent', value: 2 })
    expect(signalInfo(rssi(-8))).toMatchObject({ bars: 3, tone: 'success', tier: 'strong' })
    expect(signalInfo(rssi(-15))).toMatchObject({ bars: 2, tone: 'warning', tier: 'fair' })
    expect(signalInfo(rssi(-22))).toMatchObject({ bars: 1, tone: 'warning', tier: 'poor' })
    expect(signalInfo(rssi(-30))).toMatchObject({ bars: 1, tone: 'error', tier: 'bad' })
  })

  it('is absent without a sample, while disconnected, and for a local output', () => {
    expect(signalInfo(rssi(null))).toBeNull()
    expect(signalInfo(rssi(-5, false))).toBeNull()
    expect(signalInfo(output())).toBeNull()
  })

  it('greys the bars when the sample is older than 30 s, as stable does', () => {
    const now = Date.parse('2026-10-14T10:00:00Z')
    const sampled = (secondsAgo: number) =>
      makeDevice({ bluetooth: { rssi_dbm: -5, connected: true, rssi_at: new Date(now - secondsAgo * 1000).toISOString() } })
    expect(signalInfo(sampled(10), now)).toMatchObject({ stale: false, bars: 3, tone: 'success' })
    expect(signalInfo(sampled(31), now)).toMatchObject({ stale: true, bars: 0, tone: 'neutral', value: -5 })
  })

  it("measures the age on the bridge's clock when the speaker reports a timing sample", () => {
    const bridgeNow = Date.parse('2026-10-14T10:00:00Z')
    const clientAhead = bridgeNow + 120_000
    const at = (secondsAgo: number) =>
      makeDevice({
        bluetooth: { rssi_dbm: -5, connected: true, rssi_at: new Date(bridgeNow - secondsAgo * 1000).toISOString() },
        timing: { available: true, sampled_at: new Date(bridgeNow).toISOString() },
      })
    expect(signalInfo(at(5), clientAhead)!.stale).toBe(false)
    expect(signalInfo(at(40), clientAhead)!.stale).toBe(true)
  })

  it('treats a sample without a time (or an unreadable one) as current', () => {
    const now = Date.parse('2026-10-14T10:00:00Z')
    expect(signalInfo(makeDevice({ bluetooth: { rssi_dbm: -5, connected: true, rssi_at: null } }), now)!.stale).toBe(false)
    expect(signalInfo(makeDevice({ bluetooth: { rssi_dbm: -5, connected: true, rssi_at: 'garbage' } }), now)!.stale).toBe(false)
  })
})

describe('groupChip', () => {
  const group = (patch: Partial<Group> = {}): Group =>
    ({ id: 'syncgroup_abcd1234', name: 'Downstairs', members: [], external_count: 0, external_members: [], playing: false, avg_volume: 50, ...patch }) as Group

  it('names the Music Assistant group and links to its settings page', () => {
    const d = makeDevice({
      playback: { group: { id: 'syncgroup_abcd1234', name: 'Downstairs' } },
      music_assistant: { syncgroup_id: 'syncgroup_abcd1234' },
    })
    const chip = groupChip(d, [], [d], 'http://ma.local:8095/')!
    expect(chip.label).toBe('Downstairs')
    expect(chip.url).toBe('http://ma.local:8095/#/settings/editplayer/syncgroup_abcd1234')
  })

  it('takes the name from the bridge groups and counts members on other bridges', () => {
    const d = makeDevice({ music_assistant: { syncgroup_id: 'syncgroup_abcd1234' }, playback: { group: { id: 'syncgroup_abcd1234' } } })
    const chip = groupChip(d, [group({ name: 'Upstairs', external_count: 2 })], [d], null)!
    expect(chip.label).toBe('Upstairs')
    expect(chip.external).toBe(2)
    expect(chip.url).toBeNull()
  })

  it('falls back to # and the last four characters of an unnamed group', () => {
    const d = makeDevice({ music_assistant: { syncgroup_id: 'syncgroup_abcd1234' }, playback: { group: { id: 'syncgroup_abcd1234' } } })
    expect(groupChip(d, [], [d], null)!.label).toBe('#1234')
  })

  it('is shown for a session group only when another speaker shares it', () => {
    const a = makeDevice({ id: 'a', playback: { group: { id: 'g1' } } })
    const b = makeDevice({ id: 'b', playback: { group: { id: 'g1' } } })
    expect(groupChip(a, [], [a], 'http://ma')).toBeNull()
    const chip = groupChip(a, [], [a, b], 'http://ma')!
    expect(chip.label).toBe('#g1')
    // Not a Music Assistant sync group: nothing to open there.
    expect(chip.url).toBeNull()
  })

  it('has no chip without a group', () => {
    expect(groupChip(makeDevice(), [], [], 'http://ma')).toBeNull()
  })

  it('builds only http(s) group links', () => {
    expect(maGroupUrl('javascript:alert(1)', 'syncgroup_x')).toBeNull()
    expect(maGroupUrl('https://ma.example', 'syncgroup_a b')).toBe('https://ma.example/#/settings/editplayer/syncgroup_a%20b')
  })
})

describe('linkChip', () => {
  it('names the Bluetooth adapter and colours it by the link', () => {
    const chip = linkChip(makeDevice({ bluetooth: { adapter: { hci: 'hci1', name: 'Kitchen dongle' } } }))
    expect(chip).toMatchObject({ kind: 'adapter', label: 'Kitchen dongle', hci: 'hci1', tone: 'success', state: 'connected' })
    expect(linkChip(makeDevice())).toMatchObject({ label: 'hci0' })
  })

  it.each([
    [{ management_enabled: false, released_by: 'auto' }, 'warning', 'autoReleased'],
    [{ management_enabled: false }, 'neutral', 'released'],
    [{ reconnecting: true }, 'warning', 'reconnecting'],
    [{ connected: false }, 'error', 'disconnected'],
  ])('tones the adapter chip for %o', (bluetooth, tone, state) => {
    expect(linkChip(makeDevice({ bluetooth }))).toMatchObject({ tone, state })
  })

  it('is neutral for a disabled speaker', () => {
    expect(linkChip(makeDevice({ enabled: false }))).toMatchObject({ tone: 'neutral', state: 'disabled' })
  })

  it('names the output of a local audio output, from the sink description when known', () => {
    expect(linkChip(output())).toEqual({ kind: 'output', label: 'alsa_output.usb', sink: 'alsa_output.usb' })
    expect(
      linkChip(output(), [{ name: 'alsa_output.usb', description: 'USB DAC' } as never]),
    ).toMatchObject({ kind: 'output', label: 'USB DAC' })
  })
})

describe('blockedHints', () => {
  const cap = (blocked_reason: string | null, extra: Record<string, unknown> = {}) => ({
    supported: true,
    currently_available: blocked_reason == null,
    blocked_reason,
    depends_on: [],
    recommended_action: null,
    ...extra,
  })

  it('lists unavailable actions that give a reason, at most two', () => {
    const d = makeDevice({
      capabilities: {
        actions: {
          reconnect: cap('Bluetooth is released.', { depends_on: ['bt_management_enabled'], recommended_action: 'toggle_bt_management' }),
          play_pause: cap('No Sendspin link.', { recommended_action: 'open_diagnostics' }),
          volume: cap('No sink.'),
          mute: cap(null),
        },
      },
    })
    const hints = blockedHints(d)
    expect(hints.map((h) => h.key)).toEqual(['reconnect', 'play_pause'])
    expect(hints[0]).toMatchObject({ message: 'Bluetooth is released.', dependsOn: ['bt_management_enabled'] })
    expect(hints[1]!.recommendedAction).toBe('open_diagnostics')
  })

  it('puts a system-muted sink first', () => {
    const d = makeDevice({
      audio: { sink_muted: true, muted: false, has_sink: true },
      capabilities: { actions: { volume: cap('No sink.') } },
    })
    expect(blockedHints(d).map((h) => h.key)).toEqual(['sink_muted', 'volume'])
  })

  it('ignores actions without a reason, unsupported ones, and a disabled speaker', () => {
    expect(blockedHints(makeDevice({ capabilities: { actions: { reconnect: cap(null, { currently_available: false }) } } }))).toEqual([])
    expect(blockedHints(makeDevice({ capabilities: { actions: { reconnect: cap('x', { supported: false }) } } }))).toEqual([])
    expect(blockedHints(makeDevice({ enabled: false, capabilities: { actions: { volume: cap('x') } } }))).toEqual([])
    expect(blockedHints(makeDevice())).toEqual([])
  })

  it('says nothing the state badge already says: handed over, standby, offline, output missing', () => {
    const actions = { reconnect: cap('Bluetooth is released.'), play_pause: cap('No Sendspin link.'), volume: cap('No sink.') }
    expect(blockedHints(makeDevice({ bluetooth: { management_enabled: false }, capabilities: { actions } }))).toEqual([])
    expect(blockedHints(makeDevice({ bluetooth: { standby: true }, capabilities: { actions } }))).toEqual([])
    expect(
      blockedHints(makeDevice({ bluetooth: { connected: false }, health: { state: 'offline' }, capabilities: { actions } })),
    ).toEqual([])
    expect(
      blockedHints(output({ audio: { sink_name: 'x', has_sink: false, sink_missing: true }, capabilities: { actions } })),
    ).toEqual([])
  })

  it('explains queue controls only while the player streams', () => {
    const actions = { queue_control: cap('No queue.') }
    expect(blockedHints(output({ capabilities: { actions } }))).toEqual([])
    const playing = makeDevice({ playback: { playing: true }, audio: { streaming: true }, capabilities: { actions } })
    expect(blockedHints(playing).map((h) => h.key)).toEqual(['queue_control'])
  })

  it('says queue controls are unavailable only when the card cannot show them', () => {
    const actions = { queue_control: cap('No queue.') }
    const native = makeDevice({
      playback: { playing: true, supported_commands: ['shuffle', 'unshuffle', 'repeat_off', 'repeat_one', 'repeat_all'] },
      audio: { streaming: true },
      capabilities: { actions },
    })
    expect(blockedHints(native).map((h) => h.key)).toEqual([])
  })

  it('never offers Bluetooth-only hints for a local output', () => {
    const d = output({
      capabilities: { actions: { reconnect: cap('No Bluetooth.'), toggle_bt_management: cap('No Bluetooth.'), volume: cap('No sink.') } },
    })
    expect(blockedHints(d).map((h) => h.key)).toEqual(['volume'])
  })
})

describe('transferChip', () => {
  it('is absent without a readiness payload', () => {
    expect(transferChip(makeDevice())).toBeNull()
  })

  it('is absent for a ready player: readiness is only news when it is missing', () => {
    expect(transferChip(makeDevice({ transfer_readiness: { ready: true, reason: 'ready', severity: 'info' } }))).toBeNull()
  })

  it('colours a connected player that is not ready by severity', () => {
    expect(
      transferChip(makeDevice({ transfer_readiness: { ready: false, reason: 'bluetooth_disconnected', severity: 'error' } })),
    ).toEqual({ ready: false, tone: 'error', reason: 'bluetooth_disconnected' })
    expect(transferChip(makeDevice({ transfer_readiness: { ready: false, reason: 'stopping', severity: 'warning' } }))!.tone).toBe(
      'warning',
    )
  })

  it('stays out of the way when the link or output is down, or the state already says why', () => {
    const notReady = { ready: false, reason: 'x', severity: 'warning' }
    expect(transferChip(makeDevice({ transfer_readiness: notReady, bluetooth: { connected: false } }))).toBeNull()
    expect(transferChip(makeDevice({ transfer_readiness: notReady, audio: { has_sink: false } }))).toBeNull()
    expect(transferChip(makeDevice({ transfer_readiness: notReady, bluetooth: { standby: true } }))).toBeNull()
    expect(transferChip(makeDevice({ transfer_readiness: notReady, bluetooth: { management_enabled: false } }))).toBeNull()
    expect(transferChip(makeDevice({ transfer_readiness: notReady, enabled: false }))).toBeNull()
    expect(transferChip(makeDevice({ transfer_readiness: notReady, health: { state: 'offline' } }))).toBeNull()
  })
})

describe('serverChip', () => {
  const on = (server: Record<string, unknown>, connected = true) =>
    makeDevice({ playback: { server_connected: connected, server } })

  it('names the server the player is on, neutral when it is the bridge’s own or unknown', () => {
    const own = serverChip(on({ name: 'Music Assistant HA', host: '192.168.10.10', own: true }))!
    expect(own).toMatchObject({ label: 'Music Assistant HA', host: '192.168.10.10', tone: 'neutral', foreign: false })
    expect(serverChip(on({ host: '192.168.10.10', own: null }))).toMatchObject({ label: '192.168.10.10', foreign: false })
  })

  it('is a warning when the server is not the bridge’s own', () => {
    expect(serverChip(on({ name: 'Test MA', host: '192.168.10.229', own: false }))).toMatchObject({
      tone: 'warning',
      foreign: true,
    })
  })

  it('is hidden when the player is not connected or the server is unknown', () => {
    expect(serverChip(on({ name: 'MA', host: 'x', own: true }, false))).toBeNull()
    expect(serverChip(on({}))).toBeNull()
  })
})
