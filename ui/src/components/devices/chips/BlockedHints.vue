<script setup lang="ts">
import { computed, ref, useId } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { CircleAlert } from 'lucide-vue-next'
import { blockedHints } from '@/utils/deviceChips'
import type { Device } from '@/api/types'

/** Up to two controls the speaker cannot use now, each with a "Why?" that tells the bridge's reason. */
const props = defineProps<{ device: Device }>()
const { t, te } = useI18n()
const router = useRouter()

const hints = computed(() => blockedHints(props.device))

/* Each "Why?" is a real button that shows or hides its explanation. */
const open = ref<Record<string, boolean>>({})
const uid = useId()
const panelId = (key: string) => `blocked-why-${uid}-${key}`
function toggle(key: string) {
  open.value = { ...open.value, [key]: !open.value[key] }
}

function reason(hint: { key: string; message: string }) {
  return hint.key === 'sink_muted' ? t('device.chips.blocked.sinkMutedReason') : hint.message
}

function needs(dependsOn: string[]) {
  const labels = dependsOn.map((d) =>
    te(`device.chips.blocked.deps.${d}`) ? t(`device.chips.blocked.deps.${d}`) : d.replace(/_/g, ' '),
  )
  return labels.length ? t('device.chips.blocked.needs', { list: labels.join(' · ') }) : ''
}

function openDiagnostics() {
  void router?.push({ name: 'diagnostics' })
}
</script>

<template>
  <ul v-if="hints.length" class="space-y-1">
    <li
      v-for="hint in hints"
      :key="hint.key"
      :data-hint="hint.key"
      class="rounded-(--radius-button) border border-warning/30 bg-warning/8 px-2.5 py-1.5 text-xs text-text-primary"
    >
      <div class="flex items-center gap-1.5">
        <CircleAlert class="size-3.5 shrink-0 text-warning" aria-hidden="true" />
        <span class="min-w-0 flex-1 truncate font-medium">{{ t(`device.chips.blocked.title.${hint.key}`) }}</span>
        <button
          type="button"
          data-hint-why
          class="shrink-0 rounded text-primary-text underline-offset-2 hover:underline focus-visible:ring-2 focus-visible:ring-primary/50 focus-visible:outline-none"
          :class="{ underline: open[hint.key] }"
          :aria-expanded="open[hint.key] ? 'true' : 'false'"
          :aria-controls="panelId(hint.key)"
          @click.stop="toggle(hint.key)"
        >
          {{ t('device.chips.blocked.why') }}
        </button>
      </div>
      <div v-show="open[hint.key]" :id="panelId(hint.key)" class="mt-1 space-y-1 pl-5 text-text-secondary">
        <p>{{ reason(hint) }}</p>
        <p v-if="needs(hint.dependsOn)">{{ needs(hint.dependsOn) }}</p>
        <button
          v-if="hint.recommendedAction === 'open_diagnostics'"
          type="button"
          data-hint-diagnostics
          class="font-medium text-primary-text hover:underline"
          @click.stop="openDiagnostics"
        >
          {{ t('device.chips.blocked.openDiagnostics') }}
        </button>
      </div>
    </li>
  </ul>
</template>
