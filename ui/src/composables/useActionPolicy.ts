import { useNotificationStore } from '@/stores/notifications'
import { i18n } from '@/i18n'
import { ApiError } from '@/api/client'
import { confirmDialog, type ConfirmOptions } from '@/composables/useConfirm'

/**
 * The action policy (decision Q3), one place:
 *   undo     reversible: runs at once, a toast offers Undo           -> undoable()
 *   confirm  disruptive or irreversible: a dialog names the          -> confirmAction()
 *            consequences first (which speakers, does playback stop)
 *   choice   a question that is not destructive (keep or discard)    -> confirmAction()
 * Long operations (scan, pairing, update, restart, measurement) use
 * LongOperation (components/LongOperation.vue). Every action id is listed here;
 * tests/unit/policy/actionInventory.test.ts fails for an unlisted action or a
 * raw confirmDialog() call, so a new action cannot skip the policy.
 */
export const ACTION_POLICY = {
  'speaker.handOver': 'undo',
  'speaker.disable': 'undo',
  'speaker.enable': 'undo',
  'speaker.standby': 'undo',
  'speaker.wake': 'undo',
  'devices.muteAll': 'undo',
  'devices.powerSave': 'undo',
  'speaker.remove': 'confirm',
  'speaker.repair': 'confirm',
  'devices.removeDisabled': 'confirm',
  'devices.standbyMany': 'confirm',
  'devices.releaseMany': 'confirm',
  'adapter.powerOff': 'confirm',
  'adapter.reboot': 'confirm',
  'bluetooth.forgetBond': 'confirm',
  'config.import': 'confirm',
  'config.restart': 'confirm',
  'config.leaveUnsaved': 'confirm',
  'update.apply': 'confirm',
  'token.revoke': 'confirm',
  'sync.deleteProfile': 'confirm',
  'sync.endForeign': 'confirm',
  'sync.cancelMeasurement': 'confirm',
  'sync.stopMusic': 'confirm',
  'timing.forgetCalibration': 'confirm',
  'timing.forgetLearned': 'confirm',
  'sync.saveManualOnLeave': 'choice',
  // Hardware → Audio: the safe audio writes. Suspend silences an output, so it
  // asks first; volume, mute and resume are reversible; the default output is a
  // neutral question, not a destructive one.
  'audio.volume': 'undo',
  'audio.mute': 'undo',
  'audio.resume': 'undo',
  'audio.suspend': 'confirm',
  'audio.defaultSink': 'choice',
  // A profile or port switch is audible: the card can go silent while it
  // reconfigures, and an output can lose whatever was playing on it. Both ask.
  'audio.cardProfile': 'confirm',
  'audio.sinkPort': 'confirm',
  // Hardware → Source: starting and opening the phone's pairing window are
  // reversible (the undo is the opposite command); stopping disconnects Music
  // Assistant and forgetting the audio link needs a new code, so both ask.
  'source.maForget': 'confirm',
  'source.deviceForget': 'confirm',
  'source.pairing': 'undo',
  'source.pairingStop': 'undo',
  // Hardware → Bluetooth link: bringing the link up or down is reversible (the
  // undo is the opposite command) and destroys nothing, so it runs at once.
  'bluetooth.connect': 'undo',
  'bluetooth.disconnect': 'undo',
} as const

export type ActionId = keyof typeof ACTION_POLICY

/** Ask first. A `confirm` action gets the red button unless told otherwise. */
export function confirmAction(id: ActionId, options: ConfirmOptions): Promise<boolean> {
  const policy = ACTION_POLICY[id]
  if (policy === 'undo') throw new Error(`${id} is reversible: use undoable(), not confirmAction()`)
  return confirmDialog({ ...options, danger: options.danger ?? policy === 'confirm' })
}

export interface UndoableOptions {
  /** Toast text for the done action; gets {name}. */
  doneKey: string
  name: string
  failure: (e: unknown) => string
  /**
   * Some commands answer 200 with a refusal in the body instead of throwing.
   * When this returns false the failure is already shown inline: no toast and
   * no Undo for work that did not happen.
   */
  offerUndo?: (result: unknown) => boolean
}

/**
 * Run `forward` at once; when it works, a toast offers Undo (`backward`).
 * Resolves with what forward returned, or undefined after showing the failure.
 */
export async function undoable(
  id: ActionId,
  forward: () => Promise<unknown>,
  backward: () => Promise<unknown>,
  { doneKey, name, failure, offerUndo }: UndoableOptions,
): Promise<unknown> {
  if (ACTION_POLICY[id] !== 'undo') throw new Error(`${id} is not reversible: use confirmAction()`)
  const t = i18n.global.t
  const notifications = useNotificationStore()
  let result: unknown
  try {
    result = await forward()
  } catch (e) {
    notifications.error(failure(e))
    return undefined
  }
  if (offerUndo && !offerUndo(result)) return result
  notifications.undo(t(doneKey, { name }), t('undo.label'), async () => {
    try {
      await backward()
    } catch (e) {
      notifications.error(e instanceof ApiError ? e.message : t('common.error'))
    }
  })
  return result
}
