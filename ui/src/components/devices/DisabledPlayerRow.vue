<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useBridgeStore } from '@/stores/bridge'
import { useAudioStore } from '@/stores/audio'
import { disabledLinkChip } from '@/utils/deviceChips'
import { speakerName } from '@/utils/speakerName'
import { Bluetooth, Speaker } from 'lucide-vue-next'
import DeviceStatusBadge from './DeviceStatusBadge.vue'
import DisabledPlayerMenu from './DisabledPlayerMenu.vue'
import LinkChip from './chips/LinkChip.vue'
import type { DisabledPlayer } from '@/stores/devices'

/** A disabled player in the list: the same columns as any row, dimmed, without volume or transport. */
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
const backendLabel = computed(() => t(props.player.output ? 'device.chips.backend.output' : 'device.chips.backend.bluetooth'))
</script>

<template>
  <tr data-disabled-player class="border-b border-border opacity-60 transition-colors hover:bg-surface-secondary hover:opacity-80">
    <td v-if="selectable" class="w-10 py-2 pl-3 pr-1">
      <input
        type="checkbox"
        :checked="selected"
        class="h-4 w-4 cursor-pointer rounded border-border-strong text-primary-text accent-primary"
        :aria-label="t('device.select', { name: displayName })"
        @change="emit('toggleSelect', player.id)"
      />
    </td>
    <td class="py-2 pl-3 pr-2">
      <div class="flex items-center gap-2">
        <!-- Aligns with the expand toggle of running rows -->
        <span class="-ml-1 size-6 shrink-0" aria-hidden="true" />
        <span data-backend-icon class="inline-flex shrink-0 text-text-secondary" :title="backendLabel">
          <Speaker v-if="player.output" class="h-4 w-4" aria-hidden="true" />
          <Bluetooth v-else class="h-4 w-4" aria-hidden="true" />
          <span class="sr-only">{{ backendLabel }}</span>
        </span>
        <button
          type="button"
          data-name
          class="truncate text-sm font-medium text-text-primary hover:text-primary-text"
          :title="player.name"
          @click="emit('openDetail', player.id)"
        >
          {{ displayName }}
        </button>
      </div>
      <p v-if="player.room" class="mt-1 pl-6 text-xs text-text-secondary">{{ player.room }}</p>
    </td>
    <td class="px-2 py-2"><DeviceStatusBadge data-status state="disabled" /></td>
    <td class="w-48 px-2 py-2"><span class="text-xs text-text-secondary">—</span></td>
    <td class="px-2 py-2" />
    <td class="hidden px-2 py-2 text-xs text-text-secondary md:table-cell"><LinkChip v-if="chip" :chip="chip" /></td>
    <td class="py-2 pl-2 pr-3 text-right"><DisabledPlayerMenu :player="player" @open-detail="emit('openDetail', $event)" /></td>
  </tr>
</template>
