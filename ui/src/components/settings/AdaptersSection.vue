<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { useConfigStore } from '@/stores/config'
import { useBridgeStore } from '@/stores/bridge'
import { SbBadge } from '@/kit'

const { t } = useI18n()
const configStore = useConfigStore()
const bridge = useBridgeStore()

type AdapterEntry = Record<string, unknown>

function entryFor(hci: string, mac: string | null | undefined): AdapterEntry | undefined {
  const list = (configStore.config?.BLUETOOTH_ADAPTERS ?? []) as AdapterEntry[]
  return list.find((a) => a.hci === hci || (mac && a.mac === mac))
}

/** Friendly names and device-class overrides live in BLUETOOTH_ADAPTERS, keyed by hci and MAC. */
function setEntry(hci: string, mac: string | null | undefined, patch: AdapterEntry) {
  const list = [...((configStore.config?.BLUETOOTH_ADAPTERS ?? []) as AdapterEntry[])]
  const idx = list.findIndex((a) => a.hci === hci || (mac && a.mac === mac))
  const entry = { ...(idx >= 0 ? list[idx] : {}), hci, ...(mac ? { mac } : {}), ...patch }
  if (idx >= 0) list[idx] = entry
  else list.push(entry)
  configStore.updateField('BLUETOOTH_ADAPTERS', list)
}

const inputClass =
  'h-8 w-full rounded-(--radius-input) border border-border-strong bg-surface-card px-2.5 text-sm text-text-primary outline-none focus:border-primary focus:ring-2 focus:ring-primary/25'
</script>

<template>
  <div class="py-3">
    <p class="text-sm font-medium text-text-primary">{{ t('settings.parts.adapters.label') }}</p>
    <p class="mt-0.5 text-[13px] text-text-secondary">{{ t('settings.parts.adapters.help') }}</p>
    <p v-if="bridge.adapters.length === 0" class="py-4 text-sm text-text-secondary">{{ t('config.noAdapters') }}</p>
    <ul v-else class="mt-3 divide-y divide-border rounded-(--radius-card) border border-border">
      <li v-for="a in bridge.adapters" :key="a.id" class="grid gap-2 p-3 sm:grid-cols-[1fr_14rem_10rem] sm:items-center">
        <div class="min-w-0">
          <div class="flex items-center gap-2">
            <span class="font-medium text-text-primary">{{ a.id }}</span>
            <SbBadge :tone="a.powered ? 'success' : 'neutral'" size="sm">
              {{ a.powered ? t('config.adapterPowered') : t('config.adapterOff') }}
            </SbBadge>
          </div>
          <p class="font-mono text-xs text-text-secondary">{{ a.mac }}</p>
        </div>
        <input
          :class="inputClass"
          :aria-label="t('settings.parts.adapters.name')"
          :placeholder="t('settings.parts.adapters.name')"
          :value="(entryFor(a.id, a.mac)?.name as string) ?? ''"
          @change="setEntry(a.id, a.mac, { name: ($event.target as HTMLInputElement).value.trim() })"
        />
        <input
          :class="[inputClass, 'font-mono']"
          :aria-label="t('settings.parts.adapters.deviceClass')"
          :title="t('settings.parts.adapters.deviceClassHelp')"
          placeholder="0x00010c"
          pattern="^(0x[0-9a-fA-F]{6})?$"
          :value="(entryFor(a.id, a.mac)?.device_class as string) ?? ''"
          @change="setEntry(a.id, a.mac, { device_class: ($event.target as HTMLInputElement).value.trim() })"
        />
      </li>
    </ul>
  </div>
</template>
