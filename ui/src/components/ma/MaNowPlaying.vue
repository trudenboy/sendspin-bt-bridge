<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { useMaStore } from '@/stores/ma'
import { SbButton } from '@/kit'
import { SkipBack, Play, Pause, SkipForward, Music, X } from 'lucide-vue-next'
import type { NowPlaying } from '@/api/types'

const props = defineProps<{
  groupId: string
}>()

const { t } = useI18n()
const ma = useMaStore()
const track = ref<NowPlaying | null>(null)
const imgError = ref(false)
const artworkOpen = ref(false)

onMounted(async () => {
  const data = await ma.getNowPlaying(props.groupId)
  if (data && typeof data === 'object' && 'state' in data) {
    track.value = data as NowPlaying
  }
})

function openArtwork() {
  if (track.value?.artwork_url && !imgError.value) {
    artworkOpen.value = true
  }
}

function closeArtwork() {
  artworkOpen.value = false
}

function onArtworkKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') closeArtwork()
}

onMounted(() => window.addEventListener('keydown', onArtworkKeydown))
onUnmounted(() => window.removeEventListener('keydown', onArtworkKeydown))

const isPlaying = ref(false)

async function prev() {
  await ma.queueCmd('previous', props.groupId)
}

async function togglePlay() {
  const action = isPlaying.value ? 'pause' : 'play'
  await ma.queueCmd(action, props.groupId)
  isPlaying.value = !isPlaying.value
}

async function next() {
  await ma.queueCmd('next', props.groupId)
}
</script>

<template>
  <div class="flex items-center gap-4">
    <!-- Artwork -->
    <button
      type="button"
      class="h-16 w-16 flex-shrink-0 overflow-hidden rounded-lg bg-surface-secondary"
      :class="{ 'cursor-pointer': track?.artwork_url && !imgError }"
      :aria-label="t('ma.nowPlaying.artworkPreview')"
      @click="openArtwork"
    >
      <img
        v-if="track?.artwork_url && !imgError"
        :src="track.artwork_url"
        :alt="track.title ?? t('ma.nowPlaying.artwork')"
        class="h-full w-full object-cover"
        @error="imgError = true"
      />
      <div v-else class="flex h-full w-full items-center justify-center text-text-disabled">
        <Music class="h-8 w-8" aria-hidden="true" />
      </div>
    </button>

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

    <!-- Artwork preview overlay -->
    <Teleport to="body">
      <Transition
        enter-active-class="transition-opacity duration-200 ease-out"
        leave-active-class="transition-opacity duration-150 ease-in"
        enter-from-class="opacity-0"
        enter-to-class="opacity-100"
        leave-from-class="opacity-100"
        leave-to-class="opacity-0"
      >
        <div
          v-if="artworkOpen"
          class="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
          @click.self="closeArtwork"
        >
          <div class="relative max-w-sm">
            <button
              type="button"
              class="absolute -top-3 -right-3 rounded-full bg-surface p-1 text-text-secondary shadow-lg transition-colors hover:text-text-primary"
              :aria-label="t('ma.nowPlaying.artworkClose')"
              @click="closeArtwork"
            >
              <X class="h-5 w-5" />
            </button>
            <img
              :src="track?.artwork_url"
              :alt="track?.title ?? t('ma.nowPlaying.artwork')"
              class="max-h-[80vh] w-full rounded-xl object-contain shadow-2xl"
            />
          </div>
        </div>
      </Transition>
    </Teleport>
  </div>
</template>
