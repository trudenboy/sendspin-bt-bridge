import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useBridgeStore } from '@/stores/bridge'
import { useDeviceStore, type DisabledPlayer } from '@/stores/devices'
import { useNotificationStore } from '@/stores/notifications'
import { ApiError } from '@/api/client'
import { confirmDialog } from '@/composables/useConfirm'
import { speakerName } from '@/utils/speakerName'

/** Enable (at once) or remove (after confirmation) a disabled player: card, row and bulk bar alike. */
export function useDisabledPlayerActions() {
  const { t } = useI18n()
  const bridge = useBridgeStore()
  const store = useDeviceStore()
  const notifications = useNotificationStore()
  const busy = ref(false)

  const nameOf = (p: DisabledPlayer) => speakerName(p.name, p.mac ?? p.id)
  const message = (e: unknown) => (e instanceof ApiError ? e.message : t('common.error'))

  /** Enable each; one toast names any that failed, one says a restart is needed. */
  async function enable(players: DisabledPlayer[]) {
    if (!players.length) return
    busy.value = true
    try {
      const results = await Promise.allSettled(players.map((p) => store.setEnabled(p.id, true)))
      const failed = players.filter((_, i) => results[i]!.status === 'rejected')
      const restart = results.some(
        (r) => r.status === 'fulfilled' && (r.value as { restart_required?: boolean } | undefined)?.restart_required,
      )
      await bridge.refresh()
      if (restart) notifications.warning(t('settings.restartNeeded'))
      if (failed.length === 1) notifications.error(message((results[players.indexOf(failed[0]!)] as PromiseRejectedResult).reason))
      else if (failed.length) notifications.error(t('devices.bulk.failed', { action: t('speaker.enable'), names: failed.map(nameOf).join(', ') }))
    } finally {
      busy.value = false
    }
  }

  /** Remove after one confirmation naming them all. */
  async function remove(players: DisabledPlayer[]) {
    if (!players.length) return
    const names = players.map(nameOf)
    const ok = await confirmDialog({
      title: players.length === 1 ? t('speaker.removeTitle', { name: names[0] }) : t('devices.bulk.remove.confirmTitle', { count: players.length }),
      message: players.length === 1 ? t('speaker.removeMessage') : t('devices.bulk.remove.confirmMessage', { names: names.join(', ') }),
      confirmLabel: t('speaker.remove'),
      danger: true,
    })
    if (!ok) return
    busy.value = true
    try {
      const results = await Promise.allSettled(players.map((p) => store.remove(p.id)))
      const failed = players.filter((_, i) => results[i]!.status === 'rejected').map(nameOf)
      if (failed.length) notifications.error(t('devices.bulk.failed', { action: t('speaker.remove'), names: failed.join(', ') }))
    } finally {
      busy.value = false
    }
  }

  return { busy, enable, remove, nameOf }
}
