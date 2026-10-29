import { useI18n } from 'vue-i18n'
import { useDeviceStore } from '@/stores/devices'
import { useNotificationStore } from '@/stores/notifications'
import { actionFailure, type ActionKind } from '@/utils/actionFailure'
import { ApiError } from '@/api/client'

/**
 * Reversible speaker actions run at once and offer Undo in a toast (the
 * bridge's policy: confirm only what is disruptive or irreversible).
 */
export function useReversibleActions() {
  const { t } = useI18n()
  const store = useDeviceStore()
  const notifications = useNotificationStore()

  /** Resolves with what the forward call returned (undefined when it failed, after showing the error). */
  async function reversible(
    forward: () => Promise<unknown>,
    backward: () => Promise<unknown>,
    doneKey: string,
    failure: (e: unknown) => string,
    name: string,
  ) {
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

  const handOver = (id: string, name: string) =>
    reversible(
      () => store.release(id, true),
      () => store.release(id, false),
      'undo.handedOver',
      (e) => actionFailure(t, 'release' satisfies ActionKind, name, e),
      name,
    )

  const disable = (id: string, name: string) =>
    reversible(
      () => store.setEnabled(id, false),
      () => store.setEnabled(id, true),
      'undo.disabled',
      (e) => (e instanceof ApiError ? e.message : t('device.actions.enableFailed')),
      name,
    )

  return { handOver, disable }
}
