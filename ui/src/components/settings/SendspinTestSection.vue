<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useConfigStore } from '@/stores/config'
import { testSendspin } from '@/api/config'
import { ApiError } from '@/api/client'
import { SbBadge, SbButton } from '@/kit'
import SettingsRow from './SettingsRow.vue'

const { t } = useI18n()
const configStore = useConfigStore()
const running = ref(false)
const result = ref<{ ok: boolean; summary: string } | null>(null)

async function run() {
  running.value = true
  result.value = null
  try {
    const cfg = configStore.config
    const r = (await testSendspin(cfg?.SENDSPIN_SERVER ?? undefined, cfg?.SENDSPIN_PORT ?? undefined)) as {
      status?: string
      summary?: string
    }
    result.value = { ok: r.status === 'ok', summary: r.summary ?? '' }
  } catch (e) {
    result.value = { ok: false, summary: e instanceof ApiError ? e.message : String(e) }
  } finally {
    running.value = false
  }
}
</script>

<template>
  <SettingsRow :label="t('settings.parts.sendspinTest.label')" :help="result?.summary || t('settings.parts.sendspinTest.help')">
    <div class="flex items-center gap-2">
      <SbBadge v-if="result" :tone="result.ok ? 'success' : 'error'" size="sm">
        {{ result.ok ? t('settings.parts.sendspinTest.ok') : t('settings.parts.sendspinTest.failed') }}
      </SbBadge>
      <SbButton variant="outline" size="sm" :loading="running" @click="run">{{ t('settings.parts.sendspinTest.run') }}</SbButton>
    </div>
  </SettingsRow>
</template>
