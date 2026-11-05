<script setup lang="ts">
import { useI18n } from "vue-i18n";
import { SbDropdown, SbDropdownItem } from "@/kit";
import { MoreVertical } from "lucide-vue-next";
import type { BtDeviceSummary } from "@/api/types";

defineProps<{
  device: BtDeviceSummary;
  sourceDevice?: boolean;
  canAdd: boolean;
}>();
const emit = defineEmits<{
  add: [device: BtDeviceSummary];
  forget: [device: BtDeviceSummary];
  disconnect: [device: BtDeviceSummary];
}>();
const { t } = useI18n();
</script>

<template>
  <SbDropdown align="right">
    <template #trigger>
      <button
        type="button"
        class="inline-flex size-10 items-center justify-center rounded-full text-text-secondary transition-colors hover:bg-surface-secondary hover:text-text-primary"
        :aria-label="
          t('device.actions.more', {
            name: device.alias || device.name || device.mac,
          })
        "
        aria-haspopup="true"
      >
        <MoreVertical class="size-4" />
      </button>
    </template>
    <SbDropdownItem
      v-if="canAdd"
      data-add-speaker
      @click="emit('add', device)"
      >{{ t("hardware.addAsSpeaker") }}</SbDropdownItem
    >
    <SbDropdownItem
      v-if="device.connected"
      data-disconnect-device
      @click="emit('disconnect', device)"
    >
      {{ t("hardware.disconnect") }}
    </SbDropdownItem>
    <div class="my-1 border-t border-border" />
    <SbDropdownItem
      data-forget-bond
      @click="emit('forget', device)"
      >{{ t("device.actions.forget") }}</SbDropdownItem
    >
  </SbDropdown>
</template>
