<script setup lang="ts">
import { ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useConfigStore } from '@/stores/config'
import { useNotificationStore } from '@/stores/notifications'
import { SbButton, SbCard, SbBadge } from '@/kit'

const { t } = useI18n()
const configStore = useConfigStore()
const notifications = useNotificationStore()

const rawJson = ref('')
const jsonError = ref('')
const validationResults = ref<string[]>([])

watch(
  () => configStore.config,
  (cfg) => {
    if (cfg) {
      rawJson.value = JSON.stringify(cfg, null, 2)
      jsonError.value = ''
    }
  },
  { immediate: true },
)

function applyJson() {
  try {
    const parsed = JSON.parse(rawJson.value)
    // Replace the config object fields
    Object.keys(parsed).forEach((key) => {
      configStore.updateField(key, parsed[key])
    })
    jsonError.value = ''
    notifications.success(t('config.validationOk'))
  } catch {
    jsonError.value = t('config.invalidJson')
  }
}

async function handleValidate() {
  try {
    await configStore.validateConfig()
    if (configStore.isValid) {
      validationResults.value = []
      notifications.success(t('config.configValid'))
    } else {
      validationResults.value = Object.values(configStore.validationErrors)
    }
  } catch {
    notifications.error(t('common.error'))
  }
}

const fileInputRef = ref<HTMLInputElement | null>(null)

function triggerUpload() {
  fileInputRef.value?.click()
}

async function handleFileUpload(event: Event) {
  const target = event.target as HTMLInputElement
  const file = target.files?.[0]
  if (!file) return
  try {
    await configStore.uploadConfig(file)
    notifications.success(t('config.saved'))
  } catch {
    notifications.error(t('common.error'))
  }
  target.value = ''
}

function handleDownload() {
  configStore.downloadConfig()
}
</script>

<template>
  <div class="space-y-6 py-6">
    <SbCard>
      <template #header>
        <h3 class="text-lg font-semibold text-text-primary">{{ t('config.rawJson') }}</h3>
      </template>
      <div class="space-y-4">
        <div class="flex items-start gap-2 rounded-lg border border-warning/50 bg-warning/10 p-3">
          <span class="text-warning">⚠</span>
          <p class="text-sm text-text-primary">
            {{ t('config.rawJsonWarning') }}
          </p>
        </div>

        <textarea
          v-model="rawJson"
          class="h-80 w-full rounded-lg border border-border-strong bg-surface-primary p-3 font-mono text-sm text-text-primary"
          spellcheck="false"
          data-testid="json-editor"
        />

        <p v-if="jsonError" class="text-sm text-error" role="alert">
          {{ jsonError }}
        </p>

        <div class="flex flex-wrap gap-2">
          <SbButton variant="outline" @click="applyJson">
            Apply JSON
          </SbButton>
          <SbButton variant="outline" @click="handleValidate">
            {{ t('config.validateConfig') }}
          </SbButton>
        </div>

        <div v-if="validationResults.length > 0" class="space-y-1">
          <p class="text-sm font-medium text-text-primary">{{ t('config.validationResults') }}</p>
          <div v-for="(err, idx) in validationResults" :key="idx">
            <SbBadge tone="error">{{ err }}</SbBadge>
          </div>
        </div>
      </div>
    </SbCard>

    <SbCard>
      <template #header>
        <h3 class="text-lg font-semibold text-text-primary">{{ t('config.uploadConfig') }} / {{ t('config.downloadConfig') }}</h3>
      </template>
      <div class="flex flex-wrap gap-3">
        <input
          ref="fileInputRef"
          type="file"
          accept=".json"
          class="hidden"
          data-testid="file-input"
          @change="handleFileUpload"
        />
        <SbButton variant="outline" @click="triggerUpload">
          {{ t('config.uploadConfig') }}
        </SbButton>
        <SbButton variant="outline" @click="handleDownload">
          {{ t('config.downloadConfig') }}
        </SbButton>
      </div>
    </SbCard>
  </div>
</template>
