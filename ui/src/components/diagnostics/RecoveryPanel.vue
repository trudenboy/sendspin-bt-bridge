<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useDiagnosticsStore } from '@/stores/diagnostics'
import { SbCard, SbBadge, SbButton, SbSpinner } from '@/kit'
import { ShieldCheck, RefreshCw, ClipboardCopy, DownloadCloud } from 'lucide-vue-next'
import { copyToClipboard } from '@/utils/clipboard'
import { downloadTimelineCsv } from '@/api/diagnostics'

const { t } = useI18n()
const diagnostics = useDiagnosticsStore()
const copyLabel = ref(t('diagnostics.copy'))

interface RecoveryIssue {
  key: string
  severity: string
  title: string
  summary?: string
  primary_action?: { key: string; label: string }
}

const issues = computed(() => (diagnostics.recovery?.issues ?? []) as RecoveryIssue[])

function severityTone(severity: string) {
  const map: Record<string, 'error' | 'warning' | 'info'> = {
    critical: 'error',
    error: 'error',
    warning: 'warning',
    info: 'info',
  }
  return map[severity] ?? 'neutral' as const
}

async function copyIssues() {
  const lines = issues.value.map(
    (i) => `[${i.severity}] ${i.title}: ${i.summary ?? ''}`,
  )
  const ok = await copyToClipboard(lines.join('\n'))
  copyLabel.value = ok ? t('diagnostics.copied') : t('diagnostics.copyFailed')
  setTimeout(() => { copyLabel.value = t('diagnostics.copy') }, 2000)
}

async function runChecks() {
  await diagnostics.fetchDiagnostics()
  await diagnostics.fetchRecovery()
}
</script>

<template>
  <div class="space-y-4">
    <div class="flex items-center justify-between">
      <h3 class="text-lg font-semibold text-text-primary">
        {{ t('diagnostics.recovery.title') }}
      </h3>
      <div class="flex items-center gap-2">
        <SbButton variant="outline" size="sm" :loading="diagnostics.loading" @click="runChecks">
          <template #icon-left>
            <RefreshCw class="h-4 w-4" aria-hidden="true" />
          </template>
          {{ t('diagnostics.recovery.runChecks') }}
        </SbButton>
        <SbButton v-if="issues.length > 0" variant="ghost" size="sm" @click="copyIssues">
          <template #icon-left>
            <ClipboardCopy class="h-4 w-4" aria-hidden="true" />
          </template>
          {{ copyLabel }}
        </SbButton>
        <SbButton variant="ghost" size="sm" @click="downloadTimelineCsv">
          <template #icon-left>
            <DownloadCloud class="h-4 w-4" aria-hidden="true" />
          </template>
          {{ t('diagnostics.downloadCsv') }}
        </SbButton>
      </div>
    </div>

    <div v-if="diagnostics.loading" class="flex justify-center py-8">
      <SbSpinner size="md" :label="t('common.loading')" />
    </div>

    <template v-else-if="issues.length > 0">
      <SbCard v-for="(issue, idx) in issues" :key="idx" padding="sm">
        <div class="space-y-2">
          <div class="flex items-center gap-2">
            <SbBadge :tone="severityTone(issue.severity)" size="sm" dot>
              {{ issue.severity }}
            </SbBadge>
            <span class="text-sm font-medium text-text-primary">{{ issue.title }}</span>
          </div>
          <p v-if="issue.summary" class="text-sm text-text-secondary">{{ issue.summary }}</p>
          <p v-if="issue.primary_action" class="text-xs text-text-secondary">→ {{ issue.primary_action.label }}</p>
        </div>
      </SbCard>
    </template>

    <p v-else class="flex items-center gap-2 rounded-(--radius-card) border border-border bg-surface-card px-4 py-3 text-sm text-text-secondary">
      <ShieldCheck class="size-5 shrink-0 text-success" aria-hidden="true" />
      <span><span class="font-medium text-text-primary">{{ t('diagnostics.recovery.noIssues') }}.</span> {{ t('diagnostics.recovery.noIssuesDesc') }}</span>
    </p>
  </div>
</template>
