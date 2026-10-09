<script setup lang="ts">
import { ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useConfigStore } from '@/stores/config'
import { useNotificationStore } from '@/stores/notifications'
import { ApiError } from '@/api/client'
import { SbButton } from '@/kit'
import SettingsRow from './SettingsRow.vue'

const { t } = useI18n()
const configStore = useConfigStore()
const notifications = useNotificationStore()

const fileInput = ref<HTMLInputElement | null>(null)
const editing = ref(false)
const raw = ref('')
const jsonError = ref('')

watch(editing, (open) => {
  if (open) raw.value = JSON.stringify(configStore.config, null, 2)
})

async function onFile(event: Event) {
  const target = event.target as HTMLInputElement
  const file = target.files?.[0]
  target.value = ''
  if (!file) return
  try {
    await configStore.uploadConfig(file)
    notifications.success(t('settings.parts.backup.imported'))
  } catch (e) {
    notifications.error(e instanceof ApiError ? e.message : String(e))
  }
}

/** Replaces the edited document; nothing is written until Save. */
function applyRaw() {
  try {
    const parsed = JSON.parse(raw.value)
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('not an object')
    configStore.config = parsed
    jsonError.value = ''
    editing.value = false
  } catch {
    jsonError.value = t('config.invalidJson')
  }
}
</script>

<template>
  <div>
    <SettingsRow :label="t('settings.parts.backup.export')" :help="t('settings.parts.backup.exportHelp')">
      <SbButton variant="outline" size="sm" @click="configStore.downloadConfig()">{{ t('settings.parts.backup.download') }}</SbButton>
    </SettingsRow>
    <SettingsRow :label="t('settings.parts.backup.import')" :help="t('settings.parts.backup.importHelp')">
      <input ref="fileInput" type="file" accept=".json,application/json" class="hidden" data-testid="file-input" @change="onFile" />
      <SbButton variant="outline" size="sm" @click="fileInput?.click()">{{ t('settings.parts.backup.choose') }}</SbButton>
    </SettingsRow>
    <SettingsRow :label="t('settings.parts.backup.raw')" :help="t('settings.parts.backup.rawHelp')">
      <SbButton variant="outline" size="sm" @click="editing = !editing">
        {{ editing ? t('common.cancel') : t('settings.parts.backup.edit') }}
      </SbButton>
    </SettingsRow>
    <div v-if="editing" class="space-y-2 pb-4">
      <textarea
        v-model="raw"
        spellcheck="false"
        data-testid="json-editor"
        class="h-96 w-full rounded-(--radius-input) border border-border-strong bg-surface-card p-3 font-mono text-xs text-text-primary outline-none focus:border-primary"
      />
      <p v-if="jsonError" class="text-sm text-error" role="alert">{{ jsonError }}</p>
      <SbButton variant="outline" size="sm" @click="applyRaw">{{ t('settings.parts.backup.apply') }}</SbButton>
    </div>
  </div>
</template>
