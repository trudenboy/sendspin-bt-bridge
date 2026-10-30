import { confirmAction } from '@/composables/useActionPolicy'
import { i18n } from '@/i18n'
import type { useSyncStore } from '@/stores/sync'

/**
 * The preparation's one step: the click track needs a silent group, so when the group plays
 * something else, say what will happen, stop it, then start the clicks. Resolves false when the
 * operator declined (nothing was changed).
 */
export async function stopMusicAndStartClicks(
  sync: ReturnType<typeof useSyncStore>,
  group: { state: string | null; clickerPlaying: boolean },
): Promise<boolean> {
  const t = i18n.global.t
  if (group.state === 'playing' && !group.clickerPlaying) {
    const ok = await confirmAction('sync.stopMusic', {
      title: t('sync.prep.clicker.confirmTitle'),
      message: t('sync.prep.clicker.confirmMessage'),
      confirmLabel: t('sync.prep.clicker.stopAndStart'),
    })
    if (!ok) return false
    await sync.signal('stop')
  }
  await sync.signal('start')
  return true
}
