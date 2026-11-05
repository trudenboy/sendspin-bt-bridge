<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useBridgeStore } from '@/stores/bridge'
import { useAudioStore } from '@/stores/audio'
import { disabledLinkChip } from '@/utils/deviceChips'
import LinkChip from './chips/LinkChip.vue'
import { useDisabledPlayerActions } from '@/composables/useDisabledPlayerActions'
import DeviceCardShell from './card/DeviceCardShell.vue'
import DisabledPlayerMenu from './DisabledPlayerMenu.vue'
import { disabledCardView } from './card/viewModels'
import type { DisabledPlayer } from '@/stores/devices'

const props = withDefaults(
  defineProps<{
    player: DisabledPlayer
    selectable?: boolean
    selected?: boolean
    density?: 'comfortable' | 'compact'
  }>(),
  { selectable: false, selected: false, density: 'comfortable' },
)
const emit = defineEmits<{ openDetail: [id: string]; toggleSelect: [id: string] }>()

const { t } = useI18n()
const bridge = useBridgeStore()
const audio = useAudioStore()
const actions = useDisabledPlayerActions()
const chip = computed(() => disabledLinkChip(props.player, bridge.adapters ?? [], audio.sinks))
const view = computed(() => disabledCardView(props.player))

function onAction(id: string) {
  if (id === 'enable') actions.enable([props.player])
}
</script>

<template>
  <DeviceCardShell
    :view="view"
    :density="density"
    :selectable="selectable"
    :selected="selected"
    @open-card="emit('openDetail', $event)"
    @toggle-select="emit('toggleSelect', $event)"
    @action="onAction"
  >
    <template #menu><DisabledPlayerMenu :player="player" @open-detail="emit('openDetail', $event)" /></template>
    <template #badges>
      <span v-if="bridge.adapters?.length && player.adapter" class="text-xs text-text-secondary">{{ player.adapter }}</span>
      <LinkChip v-if="chip" :chip="chip" />
    </template>
    <template #context>{{ t('device.context.disabled') }}</template>
  </DeviceCardShell>
</template>
