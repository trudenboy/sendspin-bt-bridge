<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useBridgeStore } from '@/stores/bridge'
import { useHardwareStore } from '@/stores/hardware'
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
 */
const { t } = useI18n()
const bridge = useBridgeStore()
const hardware = useHardwareStore()
const notifications = useNotificationStore()

const source = computed(() => bridge.bridge?.sendspin_source ?? null)
const enabled = computed(() => !!source.value?.enabled)
const paired = computed(() => !!source.value?.ma_paired)
const connected = computed(() => !!source.value?.ma_connected)
const code = computed(() => source.value?.ma_pairing_code ?? '')
const busy = computed(() => hardware.sourceBusy)

const statusText = computed(() => {
  if (!enabled.value) return t('hardware.source.maOff')
  if (connected.value) return t('hardware.source.maConnected')
  if (paired.value) return t('hardware.source.maReconnecting')
  return t('settings.parts.maPairing.notPaired')
})

const now = ref(Date.now())
const windowEndsAt = ref<number | null>(null)
let timer: ReturnType<typeof setInterval> | null = null
const secondsLeft = computed(() => {
  now.value
  if (windowEndsAt.value === null) return 0
  return Math.max(0, Math.ceil((windowEndsAt.value - Date.now()) / 1000))
})
const clock = (seconds: number) => `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`

function stopTicker() {
  if (timer) clearInterval(timer)
  timer = null
}
function tick() {
  now.value = Date.now()
  if (secondsLeft.value <= 0) stopTicker()
}
watch(secondsLeft, (left) => {
  if (left > 0 && timer === null) timer = setInterval(tick, 250)
})
watch(
  () => source.value?.ma_pairing_seconds_left ?? 0,
  (seconds) => {
    if (seconds > 0) {
      const end = Date.now() + seconds * 1000
      if (windowEndsAt.value === null || end > windowEndsAt.value) windowEndsAt.value = end
      return
    }
    // The server ends the window as soon as Music Assistant pairs; do not keep counting.
    if (!code.value) windowEndsAt.value = null
  },
  { immediate: true },
)
onBeforeUnmount(stopTicker)

const error = computed(() => {
  const code = source.value?.ma_error || source.value?.error || hardware.sourceError?.code
  return describeSourceCode(code, t)
})

async function pair() {
  try {
    await hardware.openMaPairingWindow()
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
    await hardware.forgetMaPairing()
    notifications.success(t('hardware.source.maForgetDone'))
  } catch {
    notifications.error(t('hardware.source.maError'))
  }
}
</script>

<template>
  <div data-ma-pairing>
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

    <div v-if="!enabled" data-ma-pairing-off class="pb-4 text-sm text-text-secondary">
      {{ t('settings.parts.maPairing.off') }}
      <RouterLink to="/hardware" class="text-primary-text underline-offset-2 hover:underline">
        {{ t('settings.parts.maPairing.offAction') }}
      </RouterLink>
    </div>

    <template v-else>
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
    </template>
  </div>
</template>
