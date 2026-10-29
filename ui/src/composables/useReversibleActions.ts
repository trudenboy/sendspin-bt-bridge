import { useI18n } from 'vue-i18n'
import { useDeviceStore } from '@/stores/devices'
import { setMute } from '@/api/playback'
import { actionFailure, type ActionKind } from '@/utils/actionFailure'
import { ApiError } from '@/api/client'
import { undoable } from '@/composables/useActionPolicy'

/**
 * Reversible speaker actions run at once and offer Undo in a toast (the
 * bridge's policy, see useActionPolicy: confirm only what is disruptive or
 * irreversible).
 */
export function useReversibleActions() {
  const { t } = useI18n()
  const store = useDeviceStore()

  const fail = (key: string) => (e: unknown) => (e instanceof ApiError ? e.message : t(key))

  const handOver = (id: string, name: string) =>
    undoable('speaker.handOver', () => store.release(id, true), () => store.release(id, false), {
      doneKey: 'undo.handedOver',
      name,
      failure: (e) => actionFailure(t, 'release' satisfies ActionKind, name, e),
    })

  const disable = (id: string, name: string) =>
    undoable('speaker.disable', () => store.setEnabled(id, false), () => store.setEnabled(id, true), {
      doneKey: 'undo.disabled',
      name,
      failure: fail('device.actions.enableFailed'),
    })

  const enable = (id: string, name: string) =>
    undoable('speaker.enable', () => store.setEnabled(id, true), () => store.setEnabled(id, false), {
      doneKey: 'undo.enabled',
      name,
      failure: fail('device.actions.enableFailed'),
    })

  const standby = (id: string, name: string) =>
    undoable('speaker.standby', () => store.standby(id), () => store.wake(id), {
      doneKey: 'undo.standby',
      name,
      failure: fail('common.error'),
    })

  const wake = (id: string, name: string) =>
    undoable('speaker.wake', () => store.wake(id), () => store.standby(id), {
      doneKey: 'undo.woke',
      name,
      failure: fail('common.error'),
    })

  /** Each speaker that took the command; one Undo reverses exactly those. Resolves with the ids that took it. */
  async function many(
    id: 'devices.muteAll' | 'devices.powerSave',
    targets: { id: string; name: string }[],
    forward: (id: string) => Promise<unknown>,
    backward: (id: string) => Promise<unknown>,
    doneKey: string,
    onFailed: (names: string[]) => void,
  ) {
    const results = await Promise.allSettled(targets.map((d) => forward(d.id)))
    const done = targets.filter((_, i) => results[i]!.status === 'fulfilled')
    const failed = targets.filter((_, i) => results[i]!.status === 'rejected').map((d) => d.name)
    if (failed.length) onFailed(failed)
    if (!done.length) return done
    await undoable(id, async () => undefined, () => Promise.all(done.map((d) => backward(d.id))), {
      doneKey,
      name: done.map((d) => d.name).join(', '),
      failure: fail('common.error'),
    })
    return done
  }

  const muteAll = (targets: { id: string; name: string }[], onFailed: (names: string[]) => void) =>
    many('devices.muteAll', targets, (id) => setMute(id, true), (id) => setMute(id, false), 'undo.muted', onFailed)

  const powerSaveAll = (targets: { id: string; name: string }[], onFailed: (names: string[]) => void) =>
    many('devices.powerSave', targets, (id) => store.setPowerSave(id, true), (id) => store.setPowerSave(id, false), 'undo.powerSave', onFailed)

  return { handOver, disable, enable, standby, wake, muteAll, powerSaveAll }
}
