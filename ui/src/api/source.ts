/* The Sendspin source: the phone's capture offered to Music Assistant as a source client. */
import { api, unwrap } from './client'
import type { SourceTargets } from './types'

/**
 * The window the bridge opens for a phone's Bluetooth pairing. The number lives here so the
 * page and the request agree; the countdown itself comes from the server's answer.
 */
export const SOURCE_PAIRING_WINDOW_S = 120

/** What the phone pairing route answers: seconds left and how the window ended, if it did. */
export interface SourcePairingWindow {
  pairing_seconds_left: number
  pairing_adapters: string[]
  error: string | null
}

/** What the Music Assistant pairing route answers: the code the operator types there. */
export interface MaPairingWindow {
  code: string
  seconds_left: number
  client_id: string | null
}

/** Start or stop the source without restarting the bridge. */
export function setSourceEnabled(enabled: boolean) {
  return unwrap(api().POST('/api/v1/bridge/source', { body: { enabled } }))
}

/** Open a Music Assistant pairing window and return its eight-digit code. */
export async function openMaPairing(): Promise<MaPairingWindow> {
  const answer = await unwrap(api().POST('/api/v1/bridge/source/ma-pairing'))
  return answer as unknown as MaPairingWindow
}

/** Forget the stored Music Assistant pairing and open a fresh window. */
export function cancelMaPairing() {
  return unwrap(api().DELETE('/api/v1/bridge/source/ma-pairing'))
}

/** Open a discoverable/pairable window so a phone can pair. */
export async function openSourcePairing(seconds = SOURCE_PAIRING_WINDOW_S): Promise<SourcePairingWindow> {
  const answer = await unwrap(api().POST('/api/v1/bridge/source/pairing', { body: { seconds } }))
  return answer as unknown as SourcePairingWindow
}

/** Close the phone pairing window now. */
export function closeSourcePairing() {
  return unwrap(api().DELETE('/api/v1/bridge/source/pairing'))
}

/** Make a paired Bluetooth device the source that is captured now. */
export function useSourceDevice(mac: string) {
  return unwrap(
    api().POST('/api/v1/bridge/source/devices/{mac}/use', {
      params: { path: { mac } },
    }),
  )
}

/** Stop capturing a device (when active) and remove its Bluetooth bond. */
export function forgetSourceDevice(mac: string) {
  return unwrap(
    api().DELETE('/api/v1/bridge/source/devices/{mac}', {
      params: { path: { mac } },
    }),
  )
}

/**
 * The target list changes when the operator adds players, not between status
 * frames. Keep one successful answer for a minute so re-opening Settings (and
 * the second component mount a route change causes) does not hammer MA.
 */
const TARGETS_TTL_MS = 60_000
let targetsCache: { value: SourceTargets; at: number } | null = null

export function peekSourceTargets(): SourceTargets | null {
  if (targetsCache && Date.now() - targetsCache.at < TARGETS_TTL_MS) return targetsCache.value
  return null
}

export function clearSourceTargetCache(): void {
  targetsCache = null
}

/** The MA players/groups the source can be pinned to, plus the saved choice. */
export async function getSourceTargets(force = false): Promise<SourceTargets> {
  if (!force) {
    const cached = peekSourceTargets()
    if (cached) return cached
  }
  const answer = (await unwrap(api().GET('/api/v1/bridge/source/targets'))) as unknown as SourceTargets
  if (answer.ma_api_available !== false && !answer.error) {
    targetsCache = { value: answer, at: Date.now() }
  }
  return answer
}

/** Pin automatic playback to one MA queue, or clear it to choose in Music Assistant. */
export async function setSourceTarget(targetPlayer: string | null) {
  return unwrap(
    api().PUT('/api/v1/bridge/source/target', {
      body: { target_player: targetPlayer },
    }),
  )
}
