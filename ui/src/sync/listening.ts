import type { ListeningDevice, ListeningDevicesAnswer, ListeningDevicesPayload } from '@/api/sync'
import type { SyncSession } from '@/api/types'

/** The devices of the canonical envelope, or of the bare array an earlier route build answered. */
export function listeningDevicesOf(answer: ListeningDevicesPayload | null | undefined): ListeningDevicesAnswer {
  if (Array.isArray(answer)) return { devices: answer }
  return { devices: answer?.devices ?? [] }
}

/** The bridge's capture sequence as the session reports it (the phone and the input paths share it). */
export type SyncSequence = NonNullable<SyncSession['sequence']>

/** The locale key of an input's kind (the source UI already names all three). */
const KIND_LABEL = {
  local: 'devices.sources.local',
  bluetooth: 'devices.sources.bluetooth',
  headset_microphone: 'devices.sources.headsetMicrophone',
} as const

export function inputKindKey(input: ListeningDevice): string {
  return KIND_LABEL[input.kind] ?? KIND_LABEL.local
}

/**
 * The sequence's own length, in seconds: the lead-in, each member alone for ``solo_s``, and the
 * ``gap_s`` between two members. The bridge uses the same number for a recording that omits the
 * client's: the last gap is silence after the last solo, so it need not be recorded.
 */
export function sequenceLengthS(sequence: SyncSequence): number {
  const members = sequence.members.length
  return sequence.lead_s + members * sequence.solo_s + Math.max(0, members - 1) * sequence.gap_s
}
