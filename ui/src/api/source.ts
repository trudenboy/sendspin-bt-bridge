/* The Sendspin source: one phone, the bridge's capture of it, and its relays. */
import { api, unwrap } from './client'

/**
 * The pairing window the bridge opens for a phone. The number lives here so the
 * page and the request agree; the countdown itself comes from the server's answer.
 */
export const SOURCE_PAIRING_WINDOW_S = 120

/** What the pairing route answers: seconds left and how the window ended, if it did. */
export interface SourcePairingWindow {
  pairing_seconds_left: number
  pairing_adapters: string[]
  error: string | null
}

/** The speaker a relay was armed on (or disarmed from). */
export interface SourcePlayerResult {
  device_id: string
  playing_source: boolean
}

/** Start or stop the source without restarting the bridge. */
export function setSourceEnabled(enabled: boolean) {
  return unwrap(api().POST('/api/v1/bridge/source', { body: { enabled } }))
}

/** Arm a speaker to play the phone's capture, or send it back to Music Assistant. */
export async function setSourcePlayer(deviceId: string, enabled: boolean): Promise<SourcePlayerResult> {
  const answer = await unwrap(
    api().POST('/api/v1/bridge/source/players', { body: { device_id: deviceId, enabled } }),
  )
  return answer as unknown as SourcePlayerResult
}

/** Open a discoverable/pairable window so a phone can pair. */
export async function openSourcePairing(seconds = SOURCE_PAIRING_WINDOW_S): Promise<SourcePairingWindow> {
  const answer = await unwrap(api().POST('/api/v1/bridge/source/pairing', { body: { seconds } }))
  return answer as unknown as SourcePairingWindow
}

/** Close the pairing window now. */
export function closeSourcePairing() {
  return unwrap(api().DELETE('/api/v1/bridge/source/pairing'))
}
