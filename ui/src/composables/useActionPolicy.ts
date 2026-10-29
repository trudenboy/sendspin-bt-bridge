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
  'devices.removeDisabled': 'confirm',
  'devices.standbyMany': 'confirm',
  'devices.releaseMany': 'confirm',
  'bluetooth.forgetBond': 'confirm',
  'adapter.powerOff': 'confirm',
  'adapter.reboot': 'confirm',
  'config.import': 'confirm',
  'config.restart': 'confirm',
  'config.leaveUnsaved': 'confirm',
  'update.apply': 'confirm',
  'token.revoke': 'confirm',
  'sync.deleteProfile': 'confirm',
  'sync.endForeign': 'confirm',
  'sync.cancelMeasurement': 'confirm',
  'timing.forgetCalibration': 'confirm',
  'timing.forgetLearned': 'confirm',
  'sync.saveManualOnLeave': 'choice',
  'timing.applyMicSuggestion': 'choice',
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
}

/**
 * Run `forward` at once; when it works, a toast offers Undo (`backward`).
 * Resolves with what forward returned, or undefined after showing the failure.
 */
export async function undoable(
  id: ActionId,
  forward: () => Promise<unknown>,
  backward: () => Promise<unknown>,
  { doneKey, name, failure }: UndoableOptions,
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
  notifications.undo(t(doneKey, { name }), t('undo.label'), async () => {
    try {
      await backward()
    } catch (e) {
      notifications.error(e instanceof ApiError ? e.message : t('common.error'))
    }
  })
  return result
}
