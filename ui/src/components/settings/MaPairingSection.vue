<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useSourceStore } from '@/stores/source'
import { useNotificationStore } from '@/stores/notifications'
import { confirmAction } from '@/composables/useActionPolicy'
import { describeSourceCode } from '@/utils/sourceErrors'
import { SbBadge, SbButton } from '@/kit'
import SettingsRow from './SettingsRow.vue'

/**
 * The bridge is a Sendspin source client of Music Assistant: before Music
 * Assistant may play the phone's audio, it must trust this bridge with an
 * eight-digit static code.  That is a property of the Music Assistant
 * connection, so it lives here, next to the account — the phone's Bluetooth
 * pairing stays on the Hardware page.
 *
 * The countdown is the source store's single clock; this section only renders
 * it. Navigating away and back must not restart or extend the window.
 */
const { t } = useI18n()
const source = useSourceStore()
const notifications = useNotificationStore()

const feed = computed(() => source.status)
const enabled = computed(() => !!feed.value?.enabled)
const paired = computed(() => !!feed.value?.ma_paired)
const connected = computed(() => !!feed.value?.ma_connected)
const code = computed(() => source.maPairingCode ?? '')
const busy = computed(() => source.busy)
const secondsLeft = computed(() => source.maPairingSecondsLeft)

const statusText = computed(() => {
  if (!enabled.value) return t('hardware.source.maOff')
  if (connected.value) return t('hardware.source.maConnected')
  if (paired.value) return t('hardware.source.maReconnecting')
  return t('settings.parts.maPairing.notPaired')
})

const clock = (seconds: number) => `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`

const error = computed(() => {
  const code = feed.value?.ma_error || feed.value?.error || source.error?.code
  return describeSourceCode(code, t)
})

async function pair() {
  try {
    await source.openMaPairing()
  } catch {
    notifications.error(t('hardware.source.maError'))
  }
}

async function forget() {
  const ok = await confirmAction('source.maForget', {
    title: t('hardware.source.maForgetConfirmTitle'),
    consequences: [t('hardware.source.maForgetConfirmBody')],
    confirmLabel: t('hardware.source.maForget'),
  })
  if (!ok) return
  try {
    await source.forgetMaPairing()
    notifications.success(t('hardware.source.maForgetDone'))
  } catch {
    notifications.error(t('hardware.source.maError'))
  }
}
</script>

<template>
  <div v-if="enabled" data-ma-pairing>
    <SettingsRow :label="t('settings.parts.maPairing.label')" :help="t('settings.parts.maPairing.help')">
      <div class="flex flex-wrap items-center gap-2">
        <SbBadge
          data-ma-pairing-status
          :tone="connected ? 'success' : paired ? 'warning' : 'neutral'"
          size="sm"
        >
          {{ connected ? t('hardware.source.maConnectedShort') : paired ? t('hardware.source.maPairedShort') : t('hardware.source.maUnpairedShort') }}
        </SbBadge>
        <SbButton
          v-if="enabled && !paired"
          size="sm"
          data-ma-pairing-pair
          :loading="busy"
          @click="pair"
        >
          {{ t('hardware.source.maPair') }}
        </SbButton>
        <SbButton
          v-else-if="enabled"
          variant="outline"
          size="sm"
          data-ma-pairing-forget
          :loading="busy"
          @click="forget"
        >
          {{ t('hardware.source.maForget') }}
        </SbButton>
      </div>
    </SettingsRow>

    <div
      v-if="code"
        data-ma-pairing-code
        class="mb-4 rounded-(--radius-card) border border-info/40 bg-info/10 px-3 py-2"
      >
        <p class="text-xs font-semibold uppercase tracking-wider text-text-secondary">
          {{ t('hardware.source.maCodeLabel') }}
        </p>
        <p class="mt-1 font-mono text-2xl font-semibold tracking-[0.2em] text-text-primary">{{ code }}</p>
        <p class="mt-1 text-xs text-text-secondary">
          {{ t('hardware.source.maCodeHint', { time: clock(secondsLeft) }) }}
        </p>
        <p class="mt-1 text-xs text-text-secondary">{{ t('settings.parts.maPairing.whereToEnter') }}</p>
      </div>
      <p class="mb-2 text-sm text-text-secondary">{{ statusText }}</p>

      <p class="mb-4 text-xs text-text-secondary">{{ t('settings.parts.maPairing.routing') }}</p>

      <div
        v-if="error"
        data-ma-pairing-error
        role="alert"
        class="mb-4 rounded-(--radius-card) border border-error/50 bg-error/10 px-3 py-2"
      >
        <p class="text-sm text-text-primary">{{ error.text }}</p>
        <p class="mt-1 text-sm text-text-secondary">{{ error.next }}</p>
        <p class="mt-1 font-mono text-xs text-text-secondary">{{ error.code }}</p>
    </div>
  </div>
</template>
