<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { useConfigStore } from '@/stores/config'
import { useBridgeStore } from '@/stores/bridge'

const { t } = useI18n()
const configStore = useConfigStore()
const bridge = useBridgeStore()

type AreaMap = Record<string, { area_id: string; area_name?: string }>

function map(): AreaMap {
  return ((configStore.config?.HA_ADAPTER_AREA_MAP ?? {}) as AreaMap) || {}
}

/** One entry per adapter MAC; clearing the area ID removes the link. */
function set(mac: string, patch: { area_id?: string; area_name?: string }) {
  const next = { ...map() }
  const entry = { ...(next[mac] ?? { area_id: '' }), ...patch }
  if (!entry.area_id.trim()) delete next[mac]
  else next[mac] = { area_id: entry.area_id.trim(), ...(entry.area_name?.trim() ? { area_name: entry.area_name.trim() } : {}) }
  configStore.updateField('HA_ADAPTER_AREA_MAP', next)
}

const inputClass =
  'h-8 w-full rounded-(--radius-input) border border-border-strong bg-surface-card px-2.5 text-sm text-text-primary outline-none focus:border-primary focus:ring-2 focus:ring-primary/25'
</script>

<template>
  <div v-if="configStore.config?.HA_AREA_NAME_ASSIST_ENABLED && bridge.adapters.length" class="py-3">
    <p class="text-sm font-medium text-text-primary">{{ t('settings.parts.areaMap.label') }}</p>
    <p class="mt-0.5 text-[13px] text-text-secondary">{{ t('settings.parts.areaMap.help') }}</p>
    <ul class="mt-3 divide-y divide-border rounded-(--radius-card) border border-border">
      <li v-for="a in bridge.adapters" :key="a.id" class="grid gap-2 p-3 sm:grid-cols-[1fr_12rem_12rem] sm:items-center">
        <div>
          <span class="font-medium text-text-primary">{{ a.name || a.id }}</span>
          <p class="font-mono text-xs text-text-secondary">{{ a.mac }}</p>
        </div>
        <input
          :class="[inputClass, 'font-mono']"
          :aria-label="t('settings.parts.areaMap.areaId')"
          :placeholder="t('settings.parts.areaMap.areaId')"
          :value="a.mac ? (map()[a.mac]?.area_id ?? '') : ''"
          :disabled="!a.mac"
          @change="a.mac && set(a.mac, { area_id: ($event.target as HTMLInputElement).value })"
        />
        <input
          :class="inputClass"
          :aria-label="t('settings.parts.areaMap.areaName')"
          :placeholder="t('settings.parts.areaMap.areaName')"
          :value="a.mac ? (map()[a.mac]?.area_name ?? '') : ''"
          :disabled="!a.mac || !map()[a.mac]"
          @change="a.mac && set(a.mac, { area_name: ($event.target as HTMLInputElement).value })"
        />
      </li>
    </ul>
  </div>
</template>
