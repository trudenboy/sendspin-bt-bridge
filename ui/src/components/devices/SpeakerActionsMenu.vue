<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useDeviceStore } from '@/stores/devices'
import { useNotificationStore } from '@/stores/notifications'
import { claimAudio } from '@/api/devices'
import { ApiError } from '@/api/client'
import { confirmDialog } from '@/composables/useConfirm'
import { speakerName } from '@/utils/speakerName'
import { SbDropdown, SbDropdownItem } from '@/kit'
import BtDeviceInfoModal from '@/components/bluetooth/BtDeviceInfoModal.vue'
import { MoreVertical } from 'lucide-vue-next'
import type { Device } from '@/api/types'

/**
 * Everything one can do to a speaker, in the same words wherever it appears
 * (card, list row): its connection, its place on the bridge, and removal.
 * Recovery tools live in the speaker's details.
 */
const props = defineProps<{ device: Device }>()

const { t } = useI18n()
const store = useDeviceStore()
const notifications = useNotificationStore()
const infoOpen = ref(false)

const name = computed(() => speakerName(props.device.name, props.device.bluetooth.mac ?? props.device.id))
const handedOver = computed(() => props.device.bluetooth.management_enabled === false)
const standby = computed(() => props.device.bluetooth.standby === true)
const connected = computed(() => props.device.bluetooth.connected)

async function run(action: () => Promise<unknown>) {
  try {
    await action()
  } catch (e) {
    notifications.error(e instanceof ApiError ? e.message : t('common.error'))
  }
}

async function remove() {
  const ok = await confirmDialog({
    title: t('speaker.removeTitle', { name: name.value }),
    message: t('speaker.removeMessage'),
    confirmLabel: t('speaker.remove'),
    danger: true,
  })
  if (ok) await run(() => store.remove(props.device.id))
}

const heading = 'px-3 pt-2 pb-1 text-xs font-medium text-text-tertiary'
</script>

<template>
  <SbDropdown align="right">
    <template #trigger>
      <button
        type="button"
        class="inline-flex size-10 cursor-pointer items-center justify-center rounded-full text-text-secondary transition-colors hover:bg-surface-secondary hover:text-text-primary"
        :aria-label="t('device.actions.more', { name })"
      >
        <MoreVertical class="size-4" />
      </button>
    </template>

    <template v-if="device.enabled && !handedOver">
      <p :class="heading">{{ t('speaker.groups.connection') }}</p>
      <SbDropdownItem @click="run(() => store.reconnect(device.id))">{{ t('speaker.reconnect') }}</SbDropdownItem>
      <SbDropdownItem v-if="standby" @click="run(() => store.wake(device.id))">{{ t('speaker.wake') }}</SbDropdownItem>
      <SbDropdownItem v-else @click="run(() => store.standby(device.id))">{{ t('speaker.standby') }}</SbDropdownItem>
      <SbDropdownItem v-if="connected" @click="run(() => claimAudio(device.id))">{{ t('speaker.claim') }}</SbDropdownItem>
    </template>

    <p :class="heading">{{ t('speaker.groups.bridge') }}</p>
    <SbDropdownItem v-if="device.enabled && !handedOver" @click="run(() => store.release(device.id, true))">
      {{ t('speaker.handOver') }}
    </SbDropdownItem>
    <SbDropdownItem v-else-if="device.enabled" @click="run(() => store.release(device.id, false))">
      {{ t('speaker.takeBack') }}
    </SbDropdownItem>
    <SbDropdownItem @click="run(() => store.setEnabled(device.id, !device.enabled))">
      {{ device.enabled ? t('speaker.disable') : t('speaker.enable') }}
    </SbDropdownItem>
    <SbDropdownItem v-if="device.bluetooth.mac" @click="infoOpen = true">{{ t('speaker.bluetoothInfo') }}</SbDropdownItem>

    <div class="my-1 border-t border-border" />
    <SbDropdownItem :destructive="true" @click="remove">{{ t('speaker.remove') }}…</SbDropdownItem>
  </SbDropdown>

  <BtDeviceInfoModal
    v-if="device.bluetooth.mac"
    :mac="device.bluetooth.mac"
    :adapter="device.bluetooth.adapter.mac ?? ''"
    :open="infoOpen"
    @update:open="infoOpen = $event"
  />
</template>
