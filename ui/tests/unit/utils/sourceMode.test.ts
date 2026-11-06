import { describe, expect, it } from 'vitest'
import {
  headsetMicId,
  headsetMicMac,
  isSourceModeDevice,
  matchesSourceQuery,
  sectionSourceInputs,
  sourceToggle,
} from '@/utils/sourceMode'
import { makeDevice } from '../../fixtures/device'
import type { SourceInputStatus } from '@/api/types'

const MAC = 'AA:BB:CC:DD:EE:FF'

const capable = (overrides: Record<string, unknown> = {}) =>
  makeDevice({
    bluetooth: { mac: MAC, microphone_available: true, hfp_available: true },
    ...overrides,
  } as never)

const headsetInput = (overrides: Record<string, unknown> = {}) =>
  ({
    id: `headset-mic:${MAC}`,
    kind: 'headset_microphone',
    name: 'Headset',
    description: 'HFP microphone',
    active: false,
    available: true,
    connected: true,
    monitor: false,
    mac: MAC,
    node: null,
    ...overrides,
  }) as unknown as SourceInputStatus

const localInput = {
  id: 'local:alsa_input.line',
  kind: 'local',
  name: 'Turntable',
  description: 'Line In',
  active: false,
  available: true,
  connected: false,
  monitor: false,
  mac: null,
  node: 'alsa_input.line',
} as unknown as SourceInputStatus

describe('source mode identity', () => {
  it('builds the input id the server uses for a headset microphone', () => {
    expect(headsetMicId('aa:bb:cc:dd:ee:ff')).toBe(`headset-mic:${MAC}`)
    expect(headsetMicMac(`headset-mic:${MAC}`)).toBe(MAC)
    expect(headsetMicMac('bluetooth:AA:BB:CC:DD:EE:FF')).toBeNull()
    expect(headsetMicMac('')).toBeNull()
    expect(headsetMicMac(null)).toBeNull()
  })

  it('moves only the device whose MAC owns the active microphone input', () => {
    const device = capable()
    expect(isSourceModeDevice(device, `headset-mic:${MAC}`)).toBe(true)
    // Address case never decides ownership.
    expect(isSourceModeDevice(device, `headset-mic:${MAC.toLowerCase()}`)).toBe(true)
    expect(isSourceModeDevice(device, `bluetooth:${MAC}`)).toBe(false)
    expect(isSourceModeDevice(device, 'local:alsa_input.line')).toBe(false)
    expect(isSourceModeDevice(device, null)).toBe(false)
    expect(isSourceModeDevice(makeDevice({ bluetooth: { mac: '11:22:33:44:55:66' } } as never), `headset-mic:${MAC}`)).toBe(false)
  })
})

describe('the card toggle', () => {
  it('offers Source on a capable player while the feature is on', () => {
    const state = sourceToggle(capable(), { featureEnabled: true, mode: 'player' })
    expect(state).toEqual({
      visible: true,
      disabled: false,
      labelKey: 'devices.sources.enterSourceMode',
      reasonKey: null,
    })
  })

  it('offers Back to playback on the same card while it is the active input', () => {
    const state = sourceToggle(capable(), { featureEnabled: true, mode: 'source' })
    expect(state.visible).toBe(true)
    expect(state.labelKey).toBe('devices.sources.backToPlayback')
  })

  it('draws nothing while the feature is off', () => {
    expect(sourceToggle(capable(), { featureEnabled: false, mode: 'player' }).visible).toBe(false)
    expect(sourceToggle(capable(), { featureEnabled: false, mode: 'source' }).visible).toBe(false)
  })

  it('draws nothing for a device without a microphone', () => {
    expect(sourceToggle(makeDevice(), { featureEnabled: true, mode: 'player' }).visible).toBe(false)
    expect(
      sourceToggle(makeDevice({ bluetooth: { microphone_available: false, hfp_available: false } } as never), {
        featureEnabled: true,
        mode: 'player',
      }).visible,
    ).toBe(false)
  })

  it('accepts an HFP-only device', () => {
    const state = sourceToggle(makeDevice({ bluetooth: { hfp_available: true } } as never), {
      featureEnabled: true,
      mode: 'player',
    })
    expect(state.visible).toBe(true)
  })

  it('is disabled with the reason that fits the device', () => {
    expect(
      sourceToggle(capable({ enabled: false }), { featureEnabled: true, mode: 'player' }),
    ).toMatchObject({ visible: true, disabled: true, reasonKey: 'devices.sources.unavailableDisabled' })
    expect(
      sourceToggle(
        capable({ bluetooth: { mac: MAC, microphone_available: true, hfp_available: true, standby: true } }),
        { featureEnabled: true, mode: 'player' },
      ),
    ).toMatchObject({ visible: true, disabled: true, reasonKey: 'devices.sources.unavailableStandby' })
    expect(
      sourceToggle(
        capable({ bluetooth: { mac: MAC, microphone_available: true, hfp_available: true, connected: false } }),
        { featureEnabled: true, mode: 'player' },
      ),
    ).toMatchObject({ visible: true, disabled: true, reasonKey: 'devices.sources.unavailableDisconnected' })
  })
})

describe('the Sources section list', () => {
  it('never lists a headset microphone row as an extra source', () => {
    expect(sectionSourceInputs([headsetInput(), localInput], new Set())).toEqual([localInput])
    expect(sectionSourceInputs([headsetInput({ active: true }), localInput], new Set([MAC]))).toEqual([localInput])
  })

  it('keeps an active microphone whose card is not on the page (a disabled player)', () => {
    const stranded = headsetInput({ active: true })
    expect(sectionSourceInputs([stranded], new Set())).toEqual([stranded])
    expect(sectionSourceInputs([stranded], new Set([MAC]))).toEqual([])
  })

  it('keeps paired phones and local inputs in the section', () => {
    const phone = { ...localInput, id: `bluetooth:${MAC}`, kind: 'bluetooth', mac: MAC, node: null } as SourceInputStatus
    expect(sectionSourceInputs([phone, localInput], new Set([MAC]))).toEqual([phone, localInput])
  })

  it('searches an input by every name the operator can see', () => {
    expect(matchesSourceQuery(localInput, '')).toBe(true)
    expect(matchesSourceQuery(localInput, 'turn')).toBe(true)
    expect(matchesSourceQuery(localInput, 'line')).toBe(true)
    expect(matchesSourceQuery(localInput, 'alsa_input')).toBe(true)
    expect(matchesSourceQuery(localInput, 'local')).toBe(true)
    expect(matchesSourceQuery(localInput, 'zzz')).toBe(false)
  })
})
