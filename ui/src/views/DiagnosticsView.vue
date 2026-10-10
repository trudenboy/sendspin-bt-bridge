<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { useDiagnosticsStore } from '@/stores/diagnostics'
import { SbButton, SbTabs } from '@/kit'
import { Bug } from 'lucide-vue-next'
import HealthSummary from '@/components/diagnostics/HealthSummary.vue'
import EventTimeline from '@/components/diagnostics/EventTimeline.vue'
import RecoveryPanel from '@/components/diagnostics/RecoveryPanel.vue'
import LogsViewer from '@/components/diagnostics/LogsViewer.vue'
import SystemPanel from '@/components/diagnostics/SystemPanel.vue'
import BugReportDialog from '@/components/BugReportDialog.vue'

const { t } = useI18n()
const diagnostics = useDiagnosticsStore()
const activeTab = ref('overview')
const bugReportOpen = ref(false)

// Overview answers "is everything fine, and if not, what happened"; the
// log and the machine details are one step further.
const tabs = [
  { id: 'overview', label: t('diagnostics.tabs.overview') },
  { id: 'logs', label: t('diagnostics.tabs.logs') },
  { id: 'system', label: t('diagnostics.tabs.system') },
]

onMounted(async () => {
  await diagnostics.fetchDiagnostics()
  await diagnostics.fetchRecovery()
})
</script>

<template>
  <div>
    <div class="mb-6 flex flex-wrap items-center justify-between gap-3">
      <h1 class="text-2xl font-semibold tracking-tight text-text-primary">{{ t('nav.diagnostics') }}</h1>
      <SbButton variant="outline" size="sm" @click="bugReportOpen = true">
        <template #icon-left><Bug class="size-4" aria-hidden="true" /></template>
        {{ t('bugreport.fileReport') }}
      </SbButton>
    </div>

    <SbTabs v-model="activeTab" :tabs="tabs">
      <template #overview>
        <div class="space-y-6 pt-4">
          <HealthSummary />
          <RecoveryPanel />
          <EventTimeline />
        </div>
      </template>
      <template #logs>
        <div class="pt-4"><LogsViewer /></div>
      </template>
      <template #system>
        <div class="pt-4"><SystemPanel /></div>
      </template>
    </SbTabs>

    <BugReportDialog v-model="bugReportOpen" />
  </div>
</template>
