/** The server's stable source error codes, in the operator's words. */
type Translate = (key: string) => string

const KEYS: Record<string, readonly [text: string, next: string]> = {
  capture_unavailable: ['hardware.source.errorCapture', 'hardware.source.errorCaptureNext'],
  headset_a2dp_restore_failed: ['hardware.source.errorHeadsetA2dp', 'hardware.source.errorHeadsetA2dpNext'],
  no_adapter: ['hardware.source.errorNoAdapter', 'hardware.source.errorBluetoothNext'],
  no_agent: ['hardware.source.errorNoAgent', 'hardware.source.errorBluetoothNext'],
  ma_unreachable: ['hardware.source.maUnreachable', 'hardware.source.maUnreachableNext'],
  ma_pairing_required: ['hardware.source.maPairingRequired', 'hardware.source.maPairingNext'],
  ma_pairing_rejected: ['hardware.source.maRejected', 'hardware.source.maPairingNext'],
  ma_source_unsupported: ['hardware.source.maUnsupported', 'hardware.source.maUnsupportedNext'],
}

export interface SourceErrorText {
  code: string
  text: string
  next: string
}

/**
 * One error code, one explanation and one next step.  The Hardware strip and
 * the Settings pairing block must never tell different stories about the same
 * failure, so both read this map.
 */
export function describeSourceCode(code: string | null | undefined, t: Translate): SourceErrorText | null {
  if (!code) return null
  const known = KEYS[code]
  return {
    code,
    text: known ? t(known[0]) : code,
    next: t(known?.[1] ?? 'hardware.source.errorNextStep'),
  }
}
