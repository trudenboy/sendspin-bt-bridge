<script setup lang="ts">
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import { SbBadge, SbStatusDot } from "@/kit";
import type { CardDot, CardTone } from "./types";

const props = defineProps<{
  labelKey: string;
  params?: Record<string, unknown>;
  tone: CardTone;
  dot: CardDot;
  title?: string;
}>();

const { t } = useI18n();
const label = computed(() => t(props.labelKey, props.params ?? {}));
</script>

<template>
  <SbBadge :tone="tone" size="sm" :title="title">
    <SbStatusDot :status="dot" size="sm" :label="label" />
    {{ label }}
  </SbBadge>
</template>
