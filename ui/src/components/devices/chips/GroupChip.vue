<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { Link2 } from 'lucide-vue-next'
import { SbBadge } from '@/kit'
import { useBridgeStore } from '@/stores/bridge'
import { groupChip } from '@/utils/deviceChips'
import { openExternal } from '@/utils/safeUrl'
import type { Device } from '@/api/types'

/** The speaker's Music Assistant group; opens the group's settings in MA when it can. */
const props = defineProps<{ device: Device }>()
const { t } = useI18n()
const bridge = useBridgeStore()

const chip = computed(() =>
  groupChip(props.device, bridge.groups, bridge.devices, bridge.bridge?.ma_web_url ?? null),
)
const title = computed(() => {
  if (!chip.value) return ''
  const lines = [t('device.chips.group.title', { name: chip.value.label }), chip.value.id]
  if (chip.value.external) lines.push(t('device.chips.group.external', { n: chip.value.external }))
  return lines.join('\n')
})

function onClick() {
  openExternal(chip.value?.url)
}
</script>

<template>
  <template v-if="chip">
    <button
      v-if="chip.url"
      type="button"
      data-chip="group"
      class="inline-flex max-w-full rounded-(--radius-badge)"
      :title="title"
      :aria-label="t('device.chips.group.open', { name: chip.label })"
      @click.stop="onClick"
    >
      <SbBadge tone="neutral" size="sm" class="max-w-full hover:brightness-95">
        <Link2 class="size-3 shrink-0" aria-hidden="true" />
        <span class="min-w-0 truncate">{{ chip.label }}</span>
        <span v-if="chip.external" class="tabular-nums">+{{ chip.external }}</span>
      </SbBadge>
    </button>
    <span v-else data-chip="group" class="inline-flex max-w-full" :title="title">
      <SbBadge tone="neutral" size="sm" class="max-w-full">
        <Link2 class="size-3 shrink-0" aria-hidden="true" />
        <span class="min-w-0 truncate">{{ chip.label }}</span>
        <span v-if="chip.external" class="tabular-nums">+{{ chip.external }}</span>
      </SbBadge>
    </span>
  </template>
</template>
