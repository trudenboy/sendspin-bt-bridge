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

/** The bridge reports "ok", "degraded" or "error" for the whole diagnostics bundle. */
const overall = computed(() => String(diagnostics.health?.status ?? 'unknown'))

const overallStatus = computed(() => {
  const map: Record<string, 'online' | 'connecting' | 'error' | 'offline'> = { ok: 'online', degraded: 'connecting', error: 'error' }
  return map[overall.value] ?? 'offline'
})

const overallLabel = computed(() => {
  const map: Record<string, string> = {
    ok: t('diagnostics.health.healthy'),
    degraded: t('diagnostics.health.degraded'),
    error: t('diagnostics.health.error'),
  }
  return map[overall.value] ?? t('diagnostics.health.unknown')
})

type Section = Record<string, unknown> | undefined

/** Each subsystem's verdict, read from the host preflight. */
function checkStatus(key: 'audio' | 'bluetooth' | 'dbus' | 'memory'): CheckStatus {
  const pre = diagnostics.preflight
  if (!pre) return 'unknown'
  if (key === 'dbus') return pre.dbus === true ? 'ok' : pre.dbus === false ? 'error' : 'unknown'
  if (key === 'memory') return typeof pre.memory_mb === 'number' && pre.memory_mb < 64 ? 'warning' : 'ok'
  const section = pre[key] as Section
  if (!section) return 'unknown'
  if (section.last_error) return 'error'
  if (key === 'bluetooth') return section.controller ? (section.daemon === 'active' ? 'ok' : 'warning') : 'error'
  return 'ok'
}

function badgeTone(status: CheckStatus) {
  const map = { ok: 'success', warning: 'warning', error: 'error', unknown: 'neutral' } as const
  return map[status]
}

const subsystems = computed<SubsystemCheck[]>(() => [
  { key: 'audio', label: t('diagnostics.health.audio'), icon: Activity, status: checkStatus('audio') },
  { key: 'bluetooth', label: t('diagnostics.health.bluetooth'), icon: Bluetooth, status: checkStatus('bluetooth') },
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
        <SbStatusDot :status="overallStatus" :label="overallLabel" size="md" />
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
            {{ t(`diagnostics.health.check.${sub.status}`) }}
          </SbBadge>
        </div>
      </SbCard>
    </div>
  </div>
</template>
