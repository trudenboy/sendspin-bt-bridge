<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { useDiagnosticsStore } from '@/stores/diagnostics'
import { SbTabs } from '@/kit'
import HealthSummary from '@/components/diagnostics/HealthSummary.vue'
import EventTimeline from '@/components/diagnostics/EventTimeline.vue'
import RecoveryPanel from '@/components/diagnostics/RecoveryPanel.vue'
import BugReportPanel from '@/components/diagnostics/BugReportPanel.vue'

const { t } = useI18n()
const diagnostics = useDiagnosticsStore()
const activeTab = ref('health')

const tabs = [
  { id: 'health', label: t('diagnostics.tabs.health') },
  { id: 'events', label: t('diagnostics.tabs.events') },
  { id: 'recovery', label: t('diagnostics.tabs.recovery') },
  { id: 'bugreport', label: t('diagnostics.tabs.bugreport') },
]

onMounted(async () => {
  await diagnostics.fetchDiagnostics()
  await diagnostics.fetchRecovery()
})
</script>

<template>
  <div>
    <h1 class="mb-6 text-2xl font-bold text-text-primary">
      {{ t('app.diagnostics') }}
    </h1>

    <SbTabs v-model="activeTab" :tabs="tabs">
      <template #health>
        <div class="pt-4">
          <HealthSummary />
        </div>
      </template>
      <template #events>
        <div class="pt-4">
          <EventTimeline />
        </div>
      </template>
      <template #recovery>
        <div class="pt-4">
          <RecoveryPanel />
        </div>
      </template>
      <template #bugreport>
        <div class="pt-4">
          <BugReportPanel />
        </div>
      </template>
    </SbTabs>
  </div>
</template>
