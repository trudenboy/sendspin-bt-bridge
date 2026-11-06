<script setup lang="ts">
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import SpeakerActionsMenu from "./SpeakerActionsMenu.vue";
import PlayerCardContext from "./PlayerCardContext.vue";
import DeviceCardShell from "./card/DeviceCardShell.vue";
import { playerCardView, sourceToggleAction } from "./card/viewModels";
import { SbBadge } from "@/kit";
import StatusState from "@/components/StatusState.vue";
import { useBridgeStore } from "@/stores/bridge";
import { useSourceStore } from "@/stores/source";
import { useNotificationStore } from "@/stores/notifications";
import { headsetMicId, sourceToggle } from "@/utils/sourceMode";
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
const bridge = useBridgeStore();
const source = useSourceStore();
const notifications = useNotificationStore();

/**
 * One control, two directions. It is the shell's primary action — the same
 * footer button on the same card — labelled "Source" here and "Back to
 * playback" once the card has moved to the Sources section.
 */
const featureEnabled = computed(
  () => !!bridge.bridge?.sendspin_source?.enabled,
);
const toggle = computed(() =>
  sourceToggle(props.device, {
    featureEnabled: featureEnabled.value,
    mode: "player",
  }),
);
const view = computed(() => {
  const base = playerCardView(props.device);
  const primary = sourceToggleAction("source-mode", toggle.value);
  return primary ? { ...base, primary } : base;
});
const microphone = computed(
  () => props.device.bluetooth?.microphone_available === true,
);

function onOpen(id: string) {
  emit("openDetail", id);
}
function onToggle(id: string) {
  emit("toggleSelect", id);
}

/** Make this player's microphone the one active input; the player itself stops. */
async function onAction(id: string) {
  if (id !== "source-mode") return;
  const mac = props.device.bluetooth?.mac;
  if (!mac) return;
  try {
    await source.selectInput(headsetMicId(mac));
  } catch {
    notifications.error(t("devices.sources.toggleFailed"));
  }
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
    @action="onAction"
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
