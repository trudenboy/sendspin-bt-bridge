<script setup lang="ts">
import { computed, nextTick, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'
import { useConfigStore } from '@/stores/config'
import { useBridgeStore } from '@/stores/bridge'
import { SbBadge, SbButton } from '@/kit'
import { ref } from 'vue'
import { Power, RotateCw } from 'lucide-vue-next'
import { setAdapterPower } from '@/api/bluetooth'
import { confirmAction } from '@/composables/useActionPolicy'
import { useNotificationStore } from '@/stores/notifications'
import { speakerName } from '@/utils/speakerName'

const { t } = useI18n()
const configStore = useConfigStore()
const bridge = useBridgeStore()
const route = useRoute()
const notifications = useNotificationStore()

/** A device card links here with ``?adapter=hci1``; that adapter is highlighted. */
const linked = computed(() => {
  const value = route?.query?.adapter
  return typeof value === 'string' ? value : null
})

/*
 * The settings page scrolls to the linked adapter on load; when the adapter
 * list arrives later, scroll once it does.
 */
const linkedListed = computed(() => linked.value != null && bridge.adapters.some((a) => a.id === linked.value))
const stopScrollWatch = watch(linkedListed, async (listed) => {
  if (!listed) return
  stopScrollWatch()
  await nextTick()
  document.getElementById(`settings-adapter-${linked.value}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
})

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

/* Power and reboot (moved here from the speaker's panel): they affect every speaker on the adapter. */
const busy = ref('')

async function powerCycle(hci: string, states: boolean[]) {
  busy.value = hci
  const reboot = states.length > 1
  try {
    for (const on of states) await setAdapterPower(hci, on)
    await bridge.refreshAdapters()
    notifications.success(t(reboot ? 'adapter.rebooted' : 'adapter.powerToggled'))
  } catch {
    notifications.error(t(reboot ? 'adapter.rebootFailed' : 'adapter.powerFailed'))
  } finally {
    busy.value = ''
  }
}

/** Every speaker on this adapter loses its link when it is powered off or rebooted. */
function consequences(hci: string) {
  const names = bridge.devices
    .filter((d) => d.bluetooth?.adapter.hci === hci)
    .map((d) => speakerName(d.name))
    .join(', ')
  return names ? [t('consequence.adapterDisconnects', { names })] : [t('adapter.confirmNone')]
}

async function onPower(a: { id: string; name?: string | null; powered?: boolean | null }) {
  const on = !!a.powered
  if (on) {
    const ok = await confirmAction('adapter.powerOff', {
      title: t('adapter.confirmOffTitle', { adapter: a.name || a.id }),
      consequences: consequences(a.id),
      confirmLabel: t('adapter.turnOff'),
    })
    if (!ok) return
  }
  void powerCycle(a.id, [!on])
}

async function onReboot(a: { id: string; name?: string | null }) {
  const ok = await confirmAction('adapter.reboot', {
    title: t('adapter.confirmRebootTitle', { adapter: a.name || a.id }),
    consequences: consequences(a.id),
    confirmLabel: t('adapter.reboot'),
  })
  if (ok) void powerCycle(a.id, [false, true])
}

const inputClass =
  'h-8 w-full rounded-(--radius-input) border border-border-strong bg-surface-card px-2.5 text-sm text-text-primary focus:border-primary'
</script>

<template>
  <div class="py-3">
    <p class="text-sm font-medium text-text-primary">{{ t('settings.parts.adapters.label') }}</p>
    <p class="mt-0.5 text-[13px] text-text-secondary">{{ t('settings.parts.adapters.help') }}</p>
    <p v-if="bridge.adapters.length === 0" class="py-4 text-sm text-text-secondary">{{ t('config.noAdapters') }}</p>
    <ul v-else class="mt-3 divide-y divide-border rounded-(--radius-card) border border-border">
      <li
        v-for="a in bridge.adapters"
        :id="`settings-adapter-${a.id}`"
        :key="a.id"
        class="grid scroll-mt-24 gap-2 p-3 transition-colors sm:grid-cols-[1fr_14rem_10rem] sm:items-center"
        :class="{ 'bg-primary/8 ring-2 ring-primary/60 ring-inset': linked === a.id }"
        :data-highlighted="linked === a.id ? 'true' : undefined"
      >
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
        <div class="flex flex-wrap items-center gap-2 sm:col-span-3">
          <SbButton variant="outline" size="sm" :loading="busy === a.id" data-adapter-power @click="onPower(a)">
            <template #icon-left><Power class="h-3.5 w-3.5" /></template>
            {{ a.powered ? t('adapter.turnOff') : t('adapter.turnOn') }}
          </SbButton>
          <SbButton variant="outline" size="sm" :loading="busy === a.id" data-adapter-reboot @click="onReboot(a)">
            <template #icon-left><RotateCw class="h-3.5 w-3.5" /></template>
            {{ t('adapter.reboot') }}
          </SbButton>
        </div>
      </li>
    </ul>
  </div>
</template>
