/* The Sendspin source: the phone's capture offered to Music Assistant as a source client. */
import { api, unwrap } from './client'

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
