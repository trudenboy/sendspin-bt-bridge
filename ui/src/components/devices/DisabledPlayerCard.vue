<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useBridgeStore } from '@/stores/bridge'
import { useAudioStore } from '@/stores/audio'
import { disabledLinkChip } from '@/utils/deviceChips'
import { speakerName } from '@/utils/speakerName'
import DeviceStatusBadge from './DeviceStatusBadge.vue'
import DisabledPlayerMenu from './DisabledPlayerMenu.vue'
import LinkChip from './chips/LinkChip.vue'
import type { DisabledPlayer } from '@/stores/devices'

/**
 * A disabled player in the grid: like any card, dimmed, with its name,
 * adapter or output and room, and no playback controls (it has no player).
 */
const props = withDefaults(defineProps<{ player: DisabledPlayer; selectable?: boolean; selected?: boolean }>(), {
  selectable: false,
  selected: false,
})
const emit = defineEmits<{ openDetail: [id: string]; toggleSelect: [id: string] }>()

const { t } = useI18n()
const bridge = useBridgeStore()
const audio = useAudioStore()

const displayName = computed(() => speakerName(props.player.name, props.player.mac ?? props.player.id))
const chip = computed(() => disabledLinkChip(props.player, bridge.adapters ?? [], audio.sinks))
</script>

<template>
  <article
    data-disabled-player
    class="relative flex flex-col gap-3 rounded-(--radius-card) border bg-surface-card p-4 opacity-60 transition-colors hover:opacity-80"
    :class="selected ? 'border-primary ring-1 ring-primary' : 'border-border hover:border-border-strong'"
  >
    <header class="flex items-start gap-2">
      <input
        v-if="selectable"
        type="checkbox"
        :checked="selected"
        class="relative z-10 mt-1 size-4 shrink-0 cursor-pointer accent-primary"
        :aria-label="t('device.select', { name: displayName })"
        @change="emit('toggleSelect', player.id)"
      />
      <div class="min-w-0 flex-1">
        <button
          type="button"
          data-name
          class="block max-w-full text-left text-base font-medium leading-snug text-text-primary after:absolute after:inset-0 after:rounded-(--radius-card) after:content-[''] focus-visible:outline-none focus-visible:after:ring-2 focus-visible:after:ring-(--sb-focus-ring)"
          :title="player.name"
          @click="emit('openDetail', player.id)"
        >
          <span class="line-clamp-2 break-words">{{ displayName }}</span>
        </button>
        <p v-if="player.room" class="mt-0.5 text-xs text-text-secondary">{{ player.room }}</p>
      </div>
      <DeviceStatusBadge data-status state="disabled" class="relative z-10 shrink-0" />
      <div class="relative z-10 -mt-1.5 -mr-2"><DisabledPlayerMenu :player="player" @open-detail="emit('openDetail', $event)" /></div>
    </header>
    <div v-if="chip" class="relative z-10 flex min-w-0 flex-wrap items-center gap-1.5">
      <LinkChip :chip="chip" />
    </div>
  </article>
</template>
