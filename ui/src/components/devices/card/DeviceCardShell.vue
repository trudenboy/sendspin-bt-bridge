<script setup lang="ts">
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import { Bluetooth, Mic, PowerOff, Volume2 } from "lucide-vue-next";
import { SbBadge, SbButton } from "@/kit";
import DeviceCardStatus from "./DeviceCardStatus.vue";
import type { CardDensity, DeviceCardRole, DeviceCardView } from "./types";

const props = withDefaults(
  defineProps<{
    view: DeviceCardView;
    density?: CardDensity;
    selectable?: boolean;
    selected?: boolean;
    loading?: boolean;
  }>(),
  {
    density: "comfortable",
    selectable: false,
    selected: false,
    loading: false,
  },
);

const emit = defineEmits<{
  "open-card": [id: string];
  "toggle-select": [id: string];
  action: [id: string];
}>();

const { t } = useI18n();

const roleIcon = computed(() => {
  const map: Record<DeviceCardRole, unknown> = {
    output: Volume2,
    input: Mic,
    bluetooth: Bluetooth,
    disabled: PowerOff,
  };
  return map[props.view.role];
});
const roleLabel = computed(() => t(`device.role.${props.view.role}`));
const compact = computed(() => props.density === "compact");
const meta = computed(() =>
  [
    props.view.meta.room,
    props.view.meta.transport,
    props.view.meta.shortId ? `#${props.view.meta.shortId}` : null,
  ].filter((part): part is string => Boolean(part)),
);
const contextText = computed(() =>
  props.view.contextKey ? t(props.view.contextKey) : "",
);

function activate() {
  emit("open-card", props.view.id);
}
</script>

<template>
  <article
    data-card
    data-device-card
    :data-role="view.role"
    :data-disabled="view.role === 'disabled' ? '' : undefined"
    :data-density="density"
    class="relative flex flex-col rounded-(--radius-card) border bg-surface-card transition-colors"
    :class="[
      selected
        ? 'border-primary ring-1 ring-primary'
        : 'border-border hover:border-border-strong',
      compact ? 'gap-2 p-3' : 'gap-3 p-4',
      view.role === 'disabled' ? 'opacity-60' : '',
      loading ? 'pointer-events-none opacity-70' : '',
    ]"
  >
    <header class="flex items-start gap-2">
      <input
        v-if="selectable"
        type="checkbox"
        :checked="selected"
        class="relative z-10 mt-1 size-4 shrink-0 cursor-pointer accent-primary"
        :aria-label="t('device.select', { name: view.identity.name })"
        @click.stop
        @change="emit('toggle-select', view.id)"
      />
      <span
        class="mt-0.5 inline-flex size-8 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary-text"
        role="img"
        :title="roleLabel"
        :aria-label="roleLabel"
      >
        <component :is="roleIcon" class="size-4" aria-hidden="true" />
      </span>
      <div class="min-w-0 flex-1">
        <button
          type="button"
          data-name
          class="block max-w-full text-left text-base font-medium leading-snug text-text-primary after:absolute after:inset-0 after:rounded-(--radius-card) after:content-[''] focus-visible:outline-none focus-visible:after:ring-2 focus-visible:after:ring-(--sb-focus-ring)"
          :title="view.identity.technicalId ?? view.identity.name"
          @click="activate"
        >
          <span class="line-clamp-2 break-words">{{ view.identity.name }}</span>
        </button>
        <p
          v-if="meta.length"
          class="mt-0.5 flex flex-wrap items-center gap-x-2 text-xs text-text-secondary"
        >
          <template v-for="(part, index) in meta" :key="`${part}-${index}`">
            <span v-if="index" aria-hidden="true">·</span>
            <span :class="part.startsWith('#') ? 'font-mono' : ''">{{
              part
            }}</span>
          </template>
        </p>
      </div>
      <DeviceCardStatus
        data-status
        :label-key="view.status.labelKey"
        :params="view.status.params"
        :tone="view.status.tone"
        :dot="view.status.dot"
        :title="view.status.title"
        class="relative z-10 shrink-0"
      />
      <div v-if="$slots.menu" class="relative z-10 -mt-1.5 -mr-2">
        <slot name="menu" />
      </div>
    </header>

    <div class="relative z-10 flex flex-wrap items-center gap-1.5">
      <SbBadge size="sm">{{ roleLabel }}</SbBadge>
      <slot name="badges" />
    </div>

    <div class="relative z-10 min-h-5 text-sm text-text-secondary">
      <slot name="context">{{ contextText }}</slot>
    </div>

    <div
      v-if="$slots.footer || view.primary"
      class="relative z-10 mt-auto flex flex-wrap items-center gap-2 pt-1"
    >
      <slot name="footer">
        <SbButton
          v-if="view.primary"
          size="sm"
          variant="outline"
          :disabled="view.primary.disabled"
          @click="emit('action', view.primary.id)"
        >
          {{ t(view.primary.labelKey) }}
        </SbButton>
      </slot>
    </div>

    <slot name="extra" />
  </article>
</template>
