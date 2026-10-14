<script setup lang="ts">
import { computed, onMounted, onUnmounted, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useMaStore } from '@/stores/ma'
import { useBridgeStore } from '@/stores/bridge'
import { useNotificationStore } from '@/stores/notifications'
import { apiUrl } from '@/api/client'
import { SbButton } from '@/kit'
import { SkipBack, Play, Pause, SkipForward } from 'lucide-vue-next'
import ArtworkThumb from '@/components/playback/ArtworkThumb.vue'

const props = defineProps<{
  groupId: string
}>()

const { t } = useI18n()
const ma = useMaStore()
const bridge = useBridgeStore()
const notifications = useNotificationStore()

/** This group's own now playing — one group playing must not show on another. */
const current = computed(() => ma.nowPlayingByGroup[props.groupId] ?? {})
const isPlaying = computed(() => current.value.state === 'playing')

/** The last track stays known while a group is idle; it is then marked as not playing. */
const track = computed(() => {
  const np = current.value
  if (!np.track) return null
  return {
    title: np.track,
    artist: np.artist,
    album: np.album,
    artwork_url: np.image_url ? apiUrl(`/${np.image_url.replace(/^\//, '')}`) : undefined,
  }
})

// Refresh with every status tick (MA's now-playing changes raise one), so a
// group started or paused elsewhere updates here too.
let pending: ReturnType<typeof setTimeout> | null = null
function refresh() {
  if (pending) clearTimeout(pending)
  pending = setTimeout(() => void ma.fetchGroupNowPlaying(props.groupId).catch(() => undefined), 250)
}
onMounted(() => void ma.fetchGroupNowPlaying(props.groupId).catch(() => undefined))
watch(() => bridge.snapshot, refresh)
onUnmounted(() => pending && clearTimeout(pending))

async function guarded(action: () => Promise<unknown>) {
  try {
    await action()
  } catch {
    notifications.error(t('transport.failed'))
  }
}

function prev() {
  void guarded(() => ma.queueCmd('previous', { syncgroup_id: props.groupId }))
}

/** Through the group's own queue, so it works for groups whose speakers are on another bridge. */
function togglePlay() {
  void guarded(() => ma.queueCmd(isPlaying.value ? 'pause' : 'play', { syncgroup_id: props.groupId }))
}

function next() {
  void guarded(() => ma.queueCmd('next', { syncgroup_id: props.groupId }))
}
</script>

<template>
  <div class="flex items-center gap-4">
    <!-- Artwork -->
    <ArtworkThumb :url="track?.artwork_url" :alt="track?.title" />

    <!-- Track info -->
    <div class="min-w-0 flex-1">
      <p class="truncate text-sm font-medium text-text-primary">
        {{ track?.title ?? t('ma.nowPlaying.noTrack') }}
      </p>
      <p v-if="track?.artist" class="truncate text-xs text-text-secondary">
        {{ track.artist }}
      </p>
      <p v-if="track?.album" class="truncate text-xs text-text-disabled">
        {{ track.album }}
      </p>
      <p v-if="track && !isPlaying" class="mt-0.5 text-xs text-text-tertiary">{{ t('ma.nowPlaying.notPlaying') }}</p>
    </div>

    <!-- Controls -->
    <div class="flex items-center gap-1">
      <SbButton variant="ghost" size="sm" icon :aria-label="t('ma.nowPlaying.prev')" @click="prev">
        <SkipBack class="h-4 w-4" aria-hidden="true" />
      </SbButton>
      <SbButton variant="primary" size="sm" icon :aria-label="t('ma.nowPlaying.playPause')" @click="togglePlay">
        <Pause v-if="isPlaying" class="h-4 w-4" aria-hidden="true" />
        <Play v-else class="h-4 w-4" aria-hidden="true" />
      </SbButton>
      <SbButton variant="ghost" size="sm" icon :aria-label="t('ma.nowPlaying.next')" @click="next">
        <SkipForward class="h-4 w-4" aria-hidden="true" />
      </SbButton>
    </div>

  </div>
</template>
