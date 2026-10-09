<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useConfigStore } from '@/stores/config'
import { SbBadge, SbButton } from '@/kit'
import { ApiError } from '@/api/client'
import {
  getCustomComponent,
  getMdns,
  getMosquitto,
  getMqttStatus,
  probeMqtt,
  testMqtt,
  type AddonState,
  type MdnsStatus,
  type MqttStatus,
} from '@/api/haIntegration'
import { getPath } from '@/settings/layout'
import SettingsRow from './SettingsRow.vue'

const { t } = useI18n()
const configStore = useConfigStore()

const mode = computed(() => getPath(configStore.config as Record<string, unknown>, 'HA_INTEGRATION.mode') ?? 'off')
const mqtt = ref<MqttStatus | null>(null)
const mosquitto = ref<AddonState | null>(null)
const component = ref<AddonState | null>(null)
const mdns = ref<MdnsStatus | null>(null)
const message = ref<{ ok: boolean; text: string } | null>(null)
const busy = ref<'' | 'probe' | 'test'>('')

async function refresh() {
  message.value = null
  try {
    if (mode.value === 'mqtt') [mqtt.value, mosquitto.value] = await Promise.all([getMqttStatus(), getMosquitto()])
    if (mode.value === 'rest') [component.value, mdns.value] = await Promise.all([getCustomComponent(), getMdns()])
  } catch {
    /* status is informational; the fields below still work */
  }
}
watch(mode, refresh, { immediate: true })

function cfg(key: string) {
  return getPath(configStore.config as Record<string, unknown>, `HA_INTEGRATION.mqtt.${key}`)
}

async function detect() {
  busy.value = 'probe'
  try {
    const found = await probeMqtt()
    if (found.found) {
      if (found.source === 'supervisor') configStore.updateField('HA_INTEGRATION.mqtt.broker', 'auto')
      else if (found.host) configStore.updateField('HA_INTEGRATION.mqtt.broker', found.host)
      if (found.port) configStore.updateField('HA_INTEGRATION.mqtt.port', found.port)
      if (found.username) configStore.updateField('HA_INTEGRATION.mqtt.username', found.username)
      if (found.ssl != null) configStore.updateField('HA_INTEGRATION.mqtt.tls', found.ssl)
    }
    message.value = { ok: found.found, text: found.hint || t(found.found ? 'settings.parts.ha.detected' : 'settings.parts.ha.notDetected') }
  } catch (e) {
    message.value = { ok: false, text: e instanceof ApiError ? e.message : String(e) }
  } finally {
    busy.value = ''
  }
}

async function test() {
  busy.value = 'test'
  try {
    const broker = String(cfg('broker') ?? '')
    const r = await testMqtt({
      host: broker,
      port: Number(cfg('port') ?? 1883),
      username: String(cfg('username') ?? ''),
      password: String(cfg('password') ?? ''),
      tls: cfg('tls') === true,
    })
    message.value = r.ok
      ? { ok: true, text: t('settings.parts.ha.testOk', { ms: r.elapsed_ms ?? 0 }) }
      : { ok: false, text: r.error || t('settings.parts.ha.testFailed') }
  } catch (e) {
    message.value = { ok: false, text: e instanceof ApiError ? e.message : String(e) }
  } finally {
    busy.value = ''
  }
}

// "auto" takes the Mosquitto add-on's credentials from the Supervisor; the
// publisher status above already shows whether that works.
const manualBroker = computed(() => {
  const b = String(cfg('broker') ?? '').trim()
  return b !== '' && b !== 'auto'
})

const publisherTone = computed(() =>
  mqtt.value?.running ? 'success' : mqtt.value?.last_error ? 'error' : 'neutral',
)
</script>

<template>
  <div>
    <template v-if="mode === 'mqtt'">
      <SettingsRow
        :label="t('settings.parts.ha.publisher')"
        :help="mqtt?.last_error || (mqtt?.broker ? t('settings.parts.ha.publisherBroker', { broker: mqtt.broker }) : t('settings.parts.ha.publisherHelp'))"
      >
        <SbBadge :tone="publisherTone" dot>{{ mqtt?.state ?? '—' }}</SbBadge>
      </SettingsRow>
      <SettingsRow
        v-if="mosquitto?.available && !mosquitto.installed"
        :label="t('settings.parts.ha.mosquitto')"
        :help="t('settings.parts.ha.mosquittoMissing')"
      >
        <a :href="mosquitto.install_url" target="_blank" rel="noopener" class="text-sm font-medium text-primary hover:underline">
          {{ t('settings.parts.ha.install') }}
        </a>
      </SettingsRow>
      <SettingsRow :label="t('settings.parts.ha.broker')" :help="message?.text || t('settings.parts.ha.brokerHelp')">
        <div class="flex items-center gap-2">
          <SbBadge v-if="message" :tone="message.ok ? 'success' : 'error'" size="sm">
            {{ message.ok ? t('settings.parts.sendspinTest.ok') : t('settings.parts.sendspinTest.failed') }}
          </SbBadge>
          <SbButton variant="outline" size="sm" :loading="busy === 'probe'" @click="detect">{{ t('settings.parts.ha.detect') }}</SbButton>
          <SbButton
            v-if="manualBroker"
            variant="outline"
            size="sm"
            :loading="busy === 'test'"
            @click="test"
          >{{ t('settings.parts.ha.test') }}</SbButton>
        </div>
      </SettingsRow>
    </template>

    <template v-else-if="mode === 'rest'">
      <SettingsRow
        :label="t('settings.parts.ha.component')"
        :help="component?.installed ? t('settings.parts.ha.componentSeen', { when: component.last_seen ?? '—' }) : t('settings.parts.ha.componentMissing')"
      >
        <SbBadge v-if="component?.installed" tone="success" dot>{{ t('settings.parts.ha.connected') }}</SbBadge>
        <a
          v-else-if="component?.install_url"
          :href="component.install_url"
          target="_blank"
          rel="noopener"
          class="text-sm font-medium text-primary hover:underline"
        >{{ t('settings.parts.ha.installHacs') }}</a>
      </SettingsRow>
      <SettingsRow :label="t('settings.parts.ha.mdns')" :help="mdns?.service_name || t('settings.parts.ha.mdnsHelp')">
        <SbBadge :tone="mdns?.advertised ? 'success' : 'neutral'" dot>
          {{ mdns?.advertised ? t('settings.parts.ha.announced') : t('settings.parts.ha.notAnnounced') }}
        </SbBadge>
      </SettingsRow>
    </template>
  </div>
</template>
