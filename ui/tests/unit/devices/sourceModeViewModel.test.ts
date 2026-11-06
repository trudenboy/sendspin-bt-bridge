import { describe, expect, it } from 'vitest'
import { sourceModeCardView, sourceToggleAction } from '@/components/devices/card/viewModels'
import type { SourceToggleState } from '@/utils/sourceMode'
import { makeDevice } from '../../fixtures/device'

const MAC = 'AA:BB:CC:DD:EE:FF'

const device = () =>
  makeDevice({
    name: 'Sony WH-1000XM4 @ bridge',
    room: { name: 'Study' },
    bluetooth: { mac: MAC, microphone_available: true, hfp_available: true },
  } as never)

describe('source mode card view model', () => {
  it('keeps the player identity and wears the input role', () => {
    const view = sourceModeCardView(device(), 'streaming', null, true)
    expect(view.role).toBe('input')
    expect(view.identity.name).toBe('Sony WH-1000XM4')
    expect(view.identity.room).toBe('Study')
    expect(view.meta.shortId).toBe('EEFF')
    expect(view.meta.room).toBe('Study')
  })

  it('says Streaming only while the capture is open', () => {
    expect(sourceModeCardView(device(), 'streaming', null, true).status.labelKey).toBe('devices.sources.streaming')
    expect(sourceModeCardView(device(), 'waiting', null, false).status.labelKey).toBe('devices.sources.waitingForStream')
    expect(sourceModeCardView(device(), 'pairing', null, false).status.labelKey).toBe('devices.sources.waitingForStream')
  })

  it('does not say Streaming for a device that is not the captured input', () => {
    expect(sourceModeCardView(device(), 'streaming', null, false).status.labelKey).toBe(
      'devices.sources.waitingForStream',
    )
  })

  it('says Error when the source itself failed', () => {
    expect(sourceModeCardView(device(), 'error', null, false).status.labelKey).toBe('devices.sources.error')
  })

  it('keeps the waiting story when a disconnect leaves the source in error', () => {
    const disconnected = makeDevice({
      bluetooth: { mac: MAC, connected: false, microphone_available: true, hfp_available: true },
    } as never)
    expect(sourceModeCardView(disconnected, 'error', null, false).status.labelKey).toBe('devices.sources.waitingForStream')
  })
})

describe('source toggle action', () => {
  const state = (overrides: Partial<SourceToggleState> = {}): SourceToggleState => ({
    visible: true,
    disabled: false,
    labelKey: 'devices.sources.enterSourceMode',
    reasonKey: null,
    ...overrides,
  })

  it('maps the toggle onto the shell primary action', () => {
    expect(sourceToggleAction('source-mode', state())).toMatchObject({
      id: 'source-mode',
      labelKey: 'devices.sources.enterSourceMode',
      disabled: false,
    })
  })

  it('carries the disabled reason as a translation key', () => {
    expect(
      sourceToggleAction('back-to-playback', state({ disabled: true, reasonKey: 'devices.sources.unavailableStandby' })),
    ).toMatchObject({
      id: 'back-to-playback',
      disabled: true,
      hintKey: 'devices.sources.unavailableStandby',
    })
  })

  it('draws nothing while the toggle is hidden', () => {
    expect(sourceToggleAction('source-mode', state({ visible: false }))).toBeUndefined()
  })
})
