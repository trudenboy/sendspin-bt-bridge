<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useNotificationStore } from '@/stores/notifications'
import { SbDialog, SbButton } from '@/kit'
import { Bug, Github, Send, Copy, Download, ChevronDown, ChevronUp } from 'lucide-vue-next'
import {
  getBugreport,
  submitBugreport,
  checkProxyAvailable,
  downloadBugreport,
  type BugreportData,
} from '@/api/diagnostics'

const model = defineModel<boolean>({ default: false })

const { t } = useI18n()
const notifications = useNotificationStore()

const title = ref('')
const description = ref('')
const email = ref('')
const method = ref<'github' | 'proxy' | 'copy'>('github')

const report = ref<BugreportData | null>(null)
const loading = ref(false)
const submitting = ref(false)
const showPreview = ref(false)
const proxyAvailable = ref(false)

const titleError = ref('')
const descError = ref('')
const emailError = ref('')

const canSubmit = computed(() => title.value.length >= 5 && description.value.length >= 10)

const methods = computed(() => {
  const list = [
    { id: 'github' as const, label: t('bugreport.method.github'), icon: Github },
    { id: 'copy' as const, label: t('bugreport.method.copy'), icon: Copy },
  ]
  if (proxyAvailable.value) {
    list.splice(1, 0, {
      id: 'proxy' as const,
      label: t('bugreport.method.proxy'),
      icon: Send,
    })
  }
  return list
})

watch(model, async (open) => {
  if (!open) return
  // Reset form
  title.value = ''
  description.value = ''
  email.value = ''
  titleError.value = ''
  descError.value = ''
  emailError.value = ''
  showPreview.value = false
  report.value = null
  method.value = 'github'

  loading.value = true
  try {
    const [reportData, proxyData] = await Promise.all([
      getBugreport(),
      checkProxyAvailable().catch(() => ({ available: false })),
    ])
    report.value = reportData
    proxyAvailable.value = proxyData.available
    if (reportData.suggested_description) {
      description.value = reportData.suggested_description
    }
  } catch {
    notifications.error(t('bugreport.loadFailed'))
  } finally {
    loading.value = false
  }
})

function validate(): boolean {
  let valid = true
  titleError.value = ''
  descError.value = ''
  emailError.value = ''

  if (title.value.length < 5) {
    titleError.value = t('bugreport.errors.titleShort')
    valid = false
  }
  if (description.value.length < 10) {
    descError.value = t('bugreport.errors.descShort')
    valid = false
  }
  if (method.value === 'proxy' && !email.value.includes('@')) {
    emailError.value = t('bugreport.errors.emailInvalid')
    valid = false
  }
  return valid
}

async function onSubmit() {
  if (!validate()) return

  submitting.value = true
  try {
    if (method.value === 'github') {
      openGitHub()
    } else if (method.value === 'proxy') {
      await submitViaProxy()
    } else {
      await copyToClipboard()
    }
  } finally {
    submitting.value = false
  }
}

function openGitHub() {
  const body = report.value?.markdown_short
    ? `${description.value}\n\n---\n\n${report.value.markdown_short}`
    : description.value
  const params = new URLSearchParams({
    title: title.value,
    body,
    labels: 'bug',
  })
  window.open(
    `https://github.com/trudenboy/sendspin-bt-bridge/issues/new?${params}`,
    '_blank',
  )
  model.value = false
}

async function submitViaProxy() {
  try {
    const result = await submitBugreport({
      title: title.value,
      description: description.value,
      email: email.value,
      diagnostics_text: report.value?.text_full,
    })
    if (result.success && result.issue_url) {
      notifications.success(t('bugreport.submitted'))
      window.open(result.issue_url, '_blank')
      model.value = false
    } else {
      notifications.error(result.error ?? t('bugreport.submitFailed'))
    }
  } catch {
    notifications.error(t('bugreport.submitFailed'))
  }
}

async function copyToClipboard() {
  const text = report.value?.text_full
    ? `# ${title.value}\n\n${description.value}\n\n---\n\n${report.value.text_full}`
    : `# ${title.value}\n\n${description.value}`
  try {
    await navigator.clipboard.writeText(text)
    notifications.success(t('bugreport.copied'))
    model.value = false
  } catch {
    notifications.error(t('bugreport.copyFailed'))
  }
}

function onDownload() {
  downloadBugreport()
}
</script>

<template>
  <SbDialog v-model="model" :title="t('bugreport.title')" width="max-w-xl">
    <div class="space-y-4">
      <!-- Title -->
      <div>
        <label class="mb-1 block text-sm font-medium text-text-primary">
          {{ t('bugreport.fields.title') }}
        </label>
        <input
          v-model="title"
          type="text"
          class="w-full rounded-lg border border-border bg-surface-primary px-3 py-2 text-sm text-text-primary placeholder:text-text-secondary focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
          :placeholder="t('bugreport.fields.titlePlaceholder')"
          maxlength="200"
        />
        <p v-if="titleError" class="mt-1 text-xs text-error">{{ titleError }}</p>
      </div>

      <!-- Description -->
      <div>
        <label class="mb-1 block text-sm font-medium text-text-primary">
          {{ t('bugreport.fields.description') }}
        </label>
        <textarea
          v-model="description"
          rows="4"
          class="w-full rounded-lg border border-border bg-surface-primary px-3 py-2 text-sm text-text-primary placeholder:text-text-secondary focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
          :placeholder="t('bugreport.fields.descPlaceholder')"
          maxlength="5000"
        />
        <p v-if="descError" class="mt-1 text-xs text-error">{{ descError }}</p>
      </div>

      <!-- Email (for proxy method) -->
      <div v-if="method === 'proxy'">
        <label class="mb-1 block text-sm font-medium text-text-primary">
          {{ t('bugreport.fields.email') }}
        </label>
        <input
          v-model="email"
          type="email"
          class="w-full rounded-lg border border-border bg-surface-primary px-3 py-2 text-sm text-text-primary placeholder:text-text-secondary focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
          placeholder="your@email.com"
        />
        <p v-if="emailError" class="mt-1 text-xs text-error">{{ emailError }}</p>
      </div>

      <!-- Diagnostics preview -->
      <div v-if="report">
        <button
          type="button"
          class="flex w-full items-center gap-1 text-sm text-text-secondary hover:text-text-primary"
          @click="showPreview = !showPreview"
        >
          <component :is="showPreview ? ChevronUp : ChevronDown" class="h-4 w-4" />
          {{ t('bugreport.diagnosticsPreview') }}
          <span class="text-xs text-text-secondary">({{ t('bugreport.autoAttached') }})</span>
        </button>
        <div
          v-if="showPreview"
          class="mt-2 max-h-48 overflow-auto rounded-lg bg-surface-secondary p-3 font-mono text-xs text-text-secondary"
        >
          <pre class="whitespace-pre-wrap">{{ report.text_full?.slice(0, 3000) }}</pre>
        </div>
      </div>

      <!-- Loading state -->
      <div v-if="loading" class="py-4 text-center text-sm text-text-secondary">
        {{ t('bugreport.loadingDiagnostics') }}
      </div>
    </div>

    <template #footer>
      <div class="flex flex-wrap items-center gap-2">
        <!-- Method selector -->
        <div class="flex rounded-lg border border-border">
          <button
            v-for="m in methods"
            :key="m.id"
            type="button"
            class="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium transition-colors first:rounded-l-lg last:rounded-r-lg"
            :class="
              method === m.id
                ? 'bg-primary text-white'
                : 'text-text-secondary hover:bg-surface-secondary'
            "
            @click="method = m.id"
          >
            <component :is="m.icon" class="h-3.5 w-3.5" />
            {{ m.label }}
          </button>
        </div>

        <div class="flex-1" />

        <SbButton variant="ghost" size="sm" @click="onDownload">
          <template #icon-left>
            <Download class="h-3.5 w-3.5" />
          </template>
          {{ t('bugreport.download') }}
        </SbButton>

        <SbButton
          size="sm"
          :loading="submitting"
          :disabled="!canSubmit || loading"
          @click="onSubmit"
        >
          <template #icon-left>
            <Bug class="h-3.5 w-3.5" />
          </template>
          {{ t('bugreport.submit') }}
        </SbButton>
      </div>
    </template>
  </SbDialog>
</template>
