import { describe, expect, it } from 'vitest'
import { playerCardView, disabledCardView, hardwareCardView, sourceCardView } from '@/components/devices/card/viewModels'
import { makeDevice } from '../../fixtures/device'
import type { BtDeviceSummary, SourceInputStatus } from '@/api/types'

const baseSource = {
  id: 'local:line',
  kind: 'local',
  name: 'Record player',
  description: 'Line in',
  active: false,
  available: true,
  connected: false,
  monitor: false,
  mac: null,
  node: 'alsa_input.line',
} as unknown as SourceInputStatus

const hardware = {
  mac: 'FC:58:FA:EB:08:6C',
  name: 'ENEBY20',
  alias: '',
  connected: true,
  paired: true,
  bonded: true,
  trusted: true,
  battery_pct: 80,
  rssi: null,
  roles: [],
  player_id: 'eneby20',
  controller_macs: [],
} as unknown as BtDeviceSummary

describe('device card view models', () => {
  it('maps a player to Output with Connected and a short id', () => {
    const view = playerCardView(makeDevice({ name: 'Kitchen @ bridge', room: { name: 'Kitchen' } }))
    expect(view.role).toBe('output')
    expect(view.identity.name).toBe('Kitchen')
    expect(view.status.labelKey).toBe('device.status.connected')
    expect(view.meta.room).toBe('Kitchen')
    expect(view.meta.shortId).toBe('EEFF')
  })

  it('maps a degraded player to Problem', () => {
    expect(playerCardView(makeDevice({ health: { state: 'degraded' } })).status.labelKey).toBe('device.status.degraded')
  })

  it('maps a source-only bond to Input', () => {
    const view = hardwareCardView(hardware, true)
    expect(view.role).toBe('input')
    expect(view.status.labelKey).toBe('hardware.connected')
    expect(view.primary?.id).toBe('open')
  })

  it('maps an unconnected source-only bond to Connect', () => {
    const view = hardwareCardView({ ...hardware, connected: false }, true)
    expect(view.primary?.id).toBe('connect')
  })

  it('maps source availability and active states', () => {
    expect(sourceCardView(baseSource, 'waiting').status.labelKey).toBe('devices.sources.available')
    expect(sourceCardView({ ...baseSource, active: true }, 'streaming').status.labelKey).toBe('devices.sources.streaming')
    expect(sourceCardView({ ...baseSource, available: false }, 'waiting').status.labelKey).toBe('devices.sources.unavailable')
  })

  it('maps a disabled player and keeps its room', () => {
    const view = disabledCardView({ id: 'dev-1', name: 'Kitchen @ bridge', mac: 'AA:BB:CC:DD:EE:FF', room: 'Kitchen' } as never)
    expect(view.role).toBe('disabled')
    expect(view.primary?.id).toBe('enable')
    expect(view.identity.room).toBe('Kitchen')
  })
})
