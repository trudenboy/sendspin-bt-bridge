<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useDiagnosticsStore } from '@/stores/diagnostics'
import { SbCard, SbStatusDot, SbBadge, SbSpinner, SbButton } from '@/kit'
import { Activity, Bluetooth, Monitor, HardDrive, ClipboardCopy } from 'lucide-vue-next'
import { copyToClipboard } from '@/utils/clipboard'

const { t } = useI18n()
const diagnostics = useDiagnosticsStore()
const copyLabel = ref(t('diagnostics.copy'))

async function copyHealth() {
  const h = diagnostics.health
  if (!h) return
  const lines = [
    `Health: ${h.status ?? 'unknown'}`,
    ...subsystems.value.map((s) => `${s.label}: ${s.status}`),
  ]
  const ok = await copyToClipboard(lines.join('\n'))
  copyLabel.value = ok ? t('diagnostics.copied') : t('diagnostics.copyFailed')
  setTimeout(() => { copyLabel.value = t('diagnostics.copy') }, 2000)
}

type CheckStatus = 'ok' | 'warning' | 'error' | 'unknown'

interface SubsystemCheck {
  key: string
  label: string
  icon: typeof Activity
  status: CheckStatus
}

const overallStatus = computed(() => {
  const s = diagnostics.health?.status ?? 'unknown'
  const map: Record<string, 'streaming' | 'ready' | 'error' | 'offline'> = {
    healthy: 'ready',
    degraded: 'error',
    error: 'error',
  }
  return map[s] ?? 'offline'
})

const overallLabel = computed(() => {
  const s = diagnostics.health?.status ?? 'unknown'
  const map: Record<string, string> = {
    healthy: t('diagnostics.health.healthy'),
    degraded: t('diagnostics.health.degraded'),
    error: t('diagnostics.health.error'),
  }
  return map[s] ?? t('diagnostics.health.unknown')
})

function checkStatus(key: string): CheckStatus {
  const checks = diagnostics.health?.checks
  if (!checks) return 'unknown'
  const val = checks[key]
  if (typeof val === 'string') return val as CheckStatus
  if (val && typeof val === 'object' && 'status' in val) {
    return (val as { status: string }).status as CheckStatus
  }
  return 'unknown'
}

function badgeTone(status: CheckStatus) {
  const map = { ok: 'success', warning: 'warning', error: 'error', unknown: 'neutral' } as const
  return map[status]
}

const subsystems = computed<SubsystemCheck[]>(() => [
  { key: 'audio_backend', label: t('diagnostics.health.audio'), icon: Activity, status: checkStatus('audio_backend') },
  { key: 'bt_controller', label: t('diagnostics.health.bluetooth'), icon: Bluetooth, status: checkStatus('bt_controller') },
  { key: 'dbus', label: t('diagnostics.health.dbus'), icon: Monitor, status: checkStatus('dbus') },
  { key: 'memory', label: t('diagnostics.health.memory'), icon: HardDrive, status: checkStatus('memory') },
])
</script>

<template>
  <div v-if="diagnostics.loading" class="flex justify-center py-12">
    <SbSpinner size="lg" :label="t('common.loading')" />
  </div>

  <div v-else-if="diagnostics.health" class="space-y-6">
    <!-- Overall health -->
    <SbCard>
      <template #header>
        <div class="flex w-full items-center justify-between">
          <span>{{ t('diagnostics.health.title') }}</span>
          <SbButton variant="ghost" size="sm" @click="copyHealth">
            <template #icon-left>
              <ClipboardCopy class="h-4 w-4" aria-hidden="true" />
            </template>
            {{ copyLabel }}
          </SbButton>
        </div>
      </template>
      <div class="flex items-center gap-3">
        <SbStatusDot :status="overallStatus" size="md" />
        <span class="text-lg font-semibold text-text-primary">{{ overallLabel }}</span>
      </div>
    </SbCard>

    <!-- Subsystem grid -->
    <div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
      <SbCard v-for="sub in subsystems" :key="sub.key" padding="sm">
        <div class="flex items-center gap-3">
          <component :is="sub.icon" class="h-5 w-5 text-text-secondary" aria-hidden="true" />
          <span class="flex-1 text-sm font-medium text-text-primary">{{ sub.label }}</span>
          <SbBadge :tone="badgeTone(sub.status)" size="sm" dot>
            {{ sub.status }}
          </SbBadge>
        </div>
      </SbCard>
    </div>
  </div>
</template>
