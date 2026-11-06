import { isDeviceConnected } from './deviceStatus'
import type { Device, SourceInputStatus } from '@/api/types'

/**
 * The two modes one Bluetooth link can be in. The same card carries both: a
 * player in the players grid, and the active input in the Sources section.
 */
export type SourceCardMode = 'player' | 'source'

/** The direction of the source-mode command a device's card is waiting on. */
export type SourceTransition = 'to-source' | 'to-playback'

/** What the card's one toggle says and whether it can be used. */
export interface SourceToggleState {
  visible: boolean
  disabled: boolean
  /** Translation key of the button's sentence. */
  labelKey: string
  /** Why the button is disabled; a translation key. */
  reasonKey: string | null
}

const HEADSET_PREFIX = 'headset-mic:'

/** A MAC in one comparable shape: the address never decides ownership by its spelling. */
function bareAddress(value: string): string {
  return value.replace(/[^0-9A-Fa-f]/g, '').toUpperCase()
}

/** The source input id a headset's microphone is addressed by on the bridge. */
export function headsetMicId(mac: string): string {
  return `${HEADSET_PREFIX}${mac.toUpperCase()}`
}

/** The MAC inside an active `headset-mic:` input id, or null for any other input. */
export function headsetMicMac(activeInput: string | null | undefined): string | null {
  const raw = String(activeInput ?? '')
  if (!raw.toLowerCase().startsWith(HEADSET_PREFIX)) return null
  return raw.slice(HEADSET_PREFIX.length).toUpperCase() || null
}

/**
 * Whether this player is the active input, i.e. its card belongs in the
 * Sources section. Only the active input moves; a player merely *able* to
 * become one stays in the players grid.
 */
export function isSourceModeDevice(device: Device, activeInput: string | null | undefined): boolean {
  const mac = headsetMicMac(activeInput)
  const deviceMac = device.bluetooth?.mac
  if (!mac || !deviceMac) return false
  return bareAddress(deviceMac) === bareAddress(mac)
}

/**
 * Whether the card belongs to the Sources section right now. A command in
 * flight pins the card to the section the operator pressed the toggle in, so a
 * status frame produced by the still-running switch cannot move it mid-press.
 */
export function isSourceSectionDevice(
  device: Device,
  activeInput: string | null | undefined,
  transition: SourceTransition | null,
): boolean {
  if (transition === 'to-source') return false
  if (transition === 'to-playback') return true
  return isSourceModeDevice(device, activeInput)
}

/**
 * The one control, two labels. It is drawn only for a capable device while
 * the feature exists, disabled with the reason the device is not ready.
 */
export function sourceToggle(
  device: Device,
  options: { featureEnabled: boolean; mode: SourceCardMode; switching?: boolean },
): SourceToggleState {
  const labelKey =
    options.mode === 'source' ? 'devices.sources.backToPlayback' : 'devices.sources.enterSourceMode'
  const capable =
    device.bluetooth?.microphone_available === true || device.bluetooth?.hfp_available === true
  if (!options.featureEnabled || !capable) {
    return { visible: false, disabled: false, labelKey, reasonKey: null }
  }
  if (options.switching) {
    // The status chip on the same card already says what is running, and a
    // second press could only queue a command that fights the first.
    return { visible: true, disabled: true, labelKey, reasonKey: null }
  }
  if (!device.enabled) {
    return { visible: true, disabled: true, labelKey, reasonKey: 'devices.sources.unavailableDisabled' }
  }
  if (device.bluetooth?.standby) {
    return { visible: true, disabled: true, labelKey, reasonKey: 'devices.sources.unavailableStandby' }
  }
  if (!isDeviceConnected(device)) {
    return { visible: true, disabled: true, labelKey, reasonKey: 'devices.sources.unavailableDisconnected' }
  }
  return { visible: true, disabled: false, labelKey, reasonKey: null }
}

/**
 * The inputs the Sources section lists. A player microphone is not an extra
 * entry: its own moving card represents it. The one exception is an active
 * microphone whose player card is not on the page (a disabled player), which
 * would otherwise leave the active input invisible with no way to release it.
 */
export function sectionSourceInputs(
  inputs: SourceInputStatus[],
  representedMacs: ReadonlySet<string>,
): SourceInputStatus[] {
  const represented = new Set([...representedMacs].map(bareAddress))
  return inputs.filter((input) => {
    if (input.kind !== 'headset_microphone') return true
    if (!input.active) return false
    const mac = input.mac
    return !mac || !represented.has(bareAddress(mac))
  })
}

/** Whether an input matches the dashboard's one search box. */
export function matchesSourceQuery(input: SourceInputStatus, query: string): boolean {
  const wanted = query.trim().toLowerCase()
  if (!wanted) return true
  return [input.name, input.description, input.mac, input.node, input.kind].some((value) =>
    (value ?? '').toLowerCase().includes(wanted),
  )
}
