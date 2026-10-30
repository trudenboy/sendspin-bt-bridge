import { BluetoothOff, Bluetooth, AudioLines, Unlink, UserCheck, UserX, UserRound } from 'lucide-vue-next'
import type { Component } from 'vue'
import type { LinkStatus } from '@/api/types'

/**
 * The three states an operator tells apart, described once (decision Q1,
 * docs/ux-glossary.md): the Audio link (a player's Sendspin stream with Music
 * Assistant), the Music Assistant account (the bridge's API sign-in) and the
 * Bluetooth connection (a speaker's link). The bridge derives the state and its
 * reason as codes; this turns a code into label, tone, icon and tooltip so the
 * header, runtime row, cards, drawer and Diagnostics all say the same thing.
 */
export type StatusKind = 'audio_link' | 'ma_account' | 'bluetooth'
export type StatusTone = 'success' | 'warning' | 'error' | 'info' | 'neutral'

export interface StatusLook {
  kind: StatusKind
  state: string
  /** The state alone ("Signed in"). */
  label: string
  /** The state's name ("Music Assistant account"). */
  name: string
  /** "Music Assistant account: Signed in" */
  full: string
  tone: StatusTone
  icon: Component
  /** Name, state, the reason and what the name means. */
  tooltip: string
}

const TONES: StatusTone[] = ['success', 'warning', 'error', 'info', 'neutral']

/** The translator of the caller: ``useI18n()``'s ``t`` and ``te``. */
export interface Translator {
  t: (key: string, params?: Record<string, unknown>) => string
  te: (key: string) => boolean
}

function iconFor(kind: StatusKind, state: string): Component {
  if (kind === 'bluetooth') return state === 'connected' || state === 'connecting' ? Bluetooth : BluetoothOff
  if (kind === 'ma_account') return state === 'signed_in' ? UserCheck : state === 'signed_out' ? UserX : UserRound
  return state === 'unavailable' || state === 'disconnected' ? Unlink : AudioLines
}

/** A state code of a newer bridge shows its own code rather than nothing. */
export function describeStatus(
  kind: StatusKind,
  status: Pick<LinkStatus, 'state' | 'tone' | 'reason'>,
  { t, te }: Translator,
): StatusLook {
  const stateKey = `status.${kind}.${status.state}`
  const label = te(stateKey) ? t(stateKey) : status.state
  const name = t(`status.kind.${kind}`)
  const reasonKey = status.reason ? `status.reason.${status.reason}` : ''
  const reason = reasonKey && te(reasonKey) ? t(reasonKey) : ''
  const tone = (TONES as string[]).includes(status.tone) ? (status.tone as StatusTone) : 'neutral'
  const parts = [`${name}: ${label}`]
  if (reason) parts.push(reason)
  parts.push(t(`status.hint.${kind}`))
  return {
    kind,
    state: status.state,
    label,
    name,
    full: `${name}: ${label}`,
    tone,
    icon: iconFor(kind, status.state),
    tooltip: parts.join('. ').replace(/\.\./g, '.'),
  }
}
