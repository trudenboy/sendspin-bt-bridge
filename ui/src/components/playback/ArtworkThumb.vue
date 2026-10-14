<script setup lang="ts">
import { onUnmounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { Music, X } from 'lucide-vue-next'

/**
 * Album art as a small square that enlarges on click (Escape or a click
 * outside closes it). A missing or broken image shows a music note instead
 * and does not enlarge.
 */
const props = withDefaults(
  defineProps<{
    url: string | null | undefined
    alt?: string | null
    /** Size of the square (Tailwind classes). */
    sizeClass?: string
    iconClass?: string
  }>(),
  { alt: null, sizeClass: 'h-16 w-16', iconClass: 'h-8 w-8' },
)

const { t } = useI18n()
const failed = ref(false)
const open = ref(false)

// A new track brings a new image: give it its own chance to load.
watch(
  () => props.url,
  () => {
    failed.value = false
  },
)

const shown = () => !!props.url && !failed.value

function enlarge() {
  if (shown()) open.value = true
}

function close() {
  open.value = false
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') close()
}

watch(open, (isOpen) => {
  if (isOpen) window.addEventListener('keydown', onKeydown)
  else window.removeEventListener('keydown', onKeydown)
})
onUnmounted(() => window.removeEventListener('keydown', onKeydown))
</script>

<template>
  <button
    type="button"
    class="flex-shrink-0 overflow-hidden rounded-lg bg-surface-secondary"
    :class="[sizeClass, { 'cursor-pointer': url && !failed, 'cursor-default': !url || failed }]"
    :aria-label="t('ma.nowPlaying.artworkPreview')"
    @click.stop="enlarge"
  >
    <img
      v-if="url && !failed"
      :src="url"
      :alt="alt ?? t('ma.nowPlaying.artwork')"
      class="h-full w-full object-cover"
      @error="failed = true"
    />
    <div v-else class="flex h-full w-full items-center justify-center text-text-disabled">
      <Music :class="iconClass" aria-hidden="true" />
    </div>
  </button>

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
        v-if="open"
        class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
        @click.self="close"
      >
        <div class="relative max-w-sm">
          <button
            type="button"
            class="absolute -top-3 -right-3 rounded-full bg-surface p-1 text-text-secondary shadow-lg transition-colors hover:text-text-primary"
            :aria-label="t('ma.nowPlaying.artworkClose')"
            @click="close"
          >
            <X class="h-5 w-5" />
          </button>
          <img
            :src="url ?? undefined"
            :alt="alt ?? t('ma.nowPlaying.artwork')"
            class="max-h-[80vh] w-full rounded-xl object-contain shadow-2xl"
          />
        </div>
      </div>
    </Transition>
  </Teleport>
</template>
