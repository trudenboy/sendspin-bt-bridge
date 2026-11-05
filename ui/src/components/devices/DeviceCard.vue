<script setup lang="ts">
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import SpeakerActionsMenu from "./SpeakerActionsMenu.vue";
import PlayerCardContext from "./PlayerCardContext.vue";
import DeviceCardShell from "./card/DeviceCardShell.vue";
import { playerCardView } from "./card/viewModels";
import { SbBadge } from "@/kit";
import StatusState from "@/components/StatusState.vue";
import type { Device } from "@/api/types";

const props = withDefaults(
  defineProps<{
    device: Device;
    selectable?: boolean;
    selected?: boolean;
    density?: 'comfortable' | 'compact';
  }>(),
  { selectable: false, selected: false, density: 'comfortable' },
);

const emit = defineEmits<{
  openDetail: [id: string];
  toggleSelect: [id: string];
}>();

const { t } = useI18n();
const view = computed(() => playerCardView(props.device));
const microphone = computed(
  () => props.device.bluetooth?.microphone_available === true,
);

function onOpen(id: string) {
  emit("openDetail", id);
}
function onToggle(id: string) {
  emit("toggleSelect", id);
}
</script>

<template>
  <DeviceCardShell
    :view="view"
    :density="density"
    :class="{ 'opacity-60': !device.enabled }"
    :selectable="selectable"
    :selected="selected"
    @open-card="onOpen"
    @toggle-select="onToggle"
  >
    <template #menu><SpeakerActionsMenu :device="device" /></template>
    <template #badges>
      <StatusState kind="bluetooth" :status="device.links.bluetooth" only-if-not-ok />
      <StatusState kind="audio_link" :status="device.links.audio_link" only-if-not-ok />
      <SbBadge v-if="microphone" size="sm" tone="info">{{ t("device.microphone") }}</SbBadge>
    </template>
    <template #context><PlayerCardContext :device="device" :selectable="selectable" /></template>
  </DeviceCardShell>
</template>
