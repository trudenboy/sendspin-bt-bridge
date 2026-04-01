<script setup lang="ts">
import { ref, computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useDiagnosticsStore } from '@/stores/diagnostics'
import { useBridgeStore } from '@/stores/bridge'
import { SbCard, SbButton } from '@/kit'
import { Download, Copy, Bug } from 'lucide-vue-next'
import BugReportDialog from '@/components/BugReportDialog.vue'

const { t } = useI18n()
const diagnostics = useDiagnosticsStore()
const bridge = useBridgeStore()

const dialogOpen = ref(false)

const systemInfo = computed(() => {
  const snap = bridge.snapshot
  if (!snap) return []
  return [
    { label: t('diagnostics.bugreport.version'), value: snap.version },
    { label: t('diagnostics.bugreport.buildDate'), value: snap.build_date },
    { label: t('diagnostics.bugreport.adapters'), value: String(snap.adapters.length) },
    { label: t('diagnostics.bugreport.devices'), value: String(snap.devices.length) },
    { label: t('diagnostics.bugreport.maConnected'), value: snap.ma_connected ? '✓' : '✗' },
  ]
})

function download() {
  diagnostics.downloadBugreport()
}

async function copyToClipboard() {
  const text = systemInfo.value.map((i) => `${i.label}: ${i.value}`).join('\n')
  await navigator.clipboard.writeText(text)
}
</script>

<template>
  <div class="space-y-6">
    <!-- System info -->
    <SbCard>
      <template #header>
        <span>{{ t('diagnostics.bugreport.systemInfo') }}</span>
      </template>
      <dl class="grid grid-cols-2 gap-x-4 gap-y-2">
        <template v-for="item in systemInfo" :key="item.label">
          <dt class="text-sm text-text-secondary">{{ item.label }}</dt>
          <dd class="text-sm font-medium text-text-primary">{{ item.value }}</dd>
        </template>
      </dl>
    </SbCard>

    <!-- Actions -->
    <div class="flex flex-wrap gap-3">
      <SbButton variant="primary" @click="dialogOpen = true">
        <template #icon-left>
          <Bug class="h-4 w-4" aria-hidden="true" />
        </template>
        {{ t('bugreport.fileReport') }}
      </SbButton>

      <SbButton variant="secondary" @click="download">
        <template #icon-left>
          <Download class="h-4 w-4" aria-hidden="true" />
        </template>
        {{ t('diagnostics.bugreport.download') }}
      </SbButton>

      <SbButton variant="secondary" @click="copyToClipboard">
        <template #icon-left>
          <Copy class="h-4 w-4" aria-hidden="true" />
        </template>
        {{ t('diagnostics.bugreport.copy') }}
      </SbButton>
    </div>

    <BugReportDialog v-model="dialogOpen" />
  </div>
</template>
