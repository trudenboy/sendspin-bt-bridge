<script setup lang="ts">
import { computed, ref } from "vue";
import { useI18n } from "vue-i18n";
import { useNotificationStore } from "@/stores/notifications";
import { transport } from "@/api/playback";
import { Play, Pause } from "lucide-vue-next";
import type { Device } from "@/api/types";
import type { MaNowPlaying } from "@/composables/usePlaybackProgress";

const props = withDefaults(defineProps<{ device: Device; selectable?: boolean }>(), { selectable: false });
const { t } = useI18n();
const notifications = useNotificationStore();
const busy = ref(false);

const nowPlaying = computed(() => {
  const track = props.device.playback.track;
  if (track.title) return { title: track.title, artist: track.artist ?? null };
  const np = props.device.music_assistant.now_playing as MaNowPlaying | null;
  if (np?.track)
    return {
      title: String(np.track),
      artist: np.artist ? String(np.artist) : null,
    };
  return null;
});
const isStreaming = computed(
  () => props.device.playback.playing && props.device.audio.streaming,
);
const supports = (command: string) => {
  const list = props.device.playback.supported_commands ?? [];
  return list.length === 0 || list.includes(command);
};
const canToggle = computed(() => supports("pause") || supports("play"));

async function toggle() {
  busy.value = true;
  try {
    await transport(props.device.id, isStreaming.value ? "pause" : "play");
  } catch {
    notifications.error(t("transport.failed"));
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <div v-if="nowPlaying" class="flex min-w-0 items-center gap-3">
    <div class="min-w-0 flex-1">
      <p class="truncate text-sm font-medium text-text-primary">
        {{ nowPlaying.title }}
      </p>
      <p v-if="nowPlaying.artist" class="truncate text-xs text-text-secondary">
        {{ nowPlaying.artist }}
      </p>
    </div>
    <button
      v-if="canToggle"
      type="button"
      class="inline-flex size-10 shrink-0 items-center justify-center rounded-full bg-primary-fill text-on-primary transition-colors hover:bg-primary-dark disabled:opacity-50"
      :aria-label="isStreaming ? t('transport.pause') : t('transport.play')"
      :disabled="busy"
      @click.stop="toggle"
    >
      <Pause v-if="isStreaming" class="size-5" />
      <Play v-else class="size-5" />
    </button>
  </div>
  <p v-else class="truncate text-sm text-text-secondary">
    {{ t("device.context.nothingPlaying") }}
  </p>
</template>
