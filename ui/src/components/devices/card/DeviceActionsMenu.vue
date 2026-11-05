<script setup lang="ts">
import { useI18n } from "vue-i18n";
import { SbDropdown, SbDropdownItem } from "@/kit";
import type { DeviceCardActionGroup } from "./types";

withDefaults(
  defineProps<{ groups: DeviceCardActionGroup[]; label: string }>(),
  { groups: () => [] },
);
const emit = defineEmits<{ action: [id: string] }>();
const { t } = useI18n();
</script>

<template>
  <SbDropdown align="right">
    <template #trigger>
      <button
        type="button"
        class="inline-flex size-10 items-center justify-center rounded-full text-text-secondary transition-colors hover:bg-surface-secondary hover:text-text-primary"
        :aria-label="label"
        aria-haspopup="true"
      >
        <span aria-hidden="true" class="text-lg leading-none">⋯</span>
      </button>
    </template>
    <template v-for="group in groups" :key="group.id">
      <p
        v-if="group.labelKey"
        class="px-3 pt-2 pb-1 text-xs font-medium text-text-tertiary"
      >
        {{ t(group.labelKey) }}
      </p>
      <SbDropdownItem
        v-for="action in group.actions"
        :key="action.id"
        :destructive="action.destructive"
        :disabled="action.disabled"
        :title="action.title"
        @click="emit('action', action.id)"
      >
        {{ t(action.labelKey) }}
      </SbDropdownItem>
    </template>
  </SbDropdown>
</template>
