<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useBridgeStore } from '@/stores/bridge'
import { SbButton } from '@/kit'
import { AlertTriangle, Info, XCircle } from 'lucide-vue-next'
import { useGuidanceActions, type GuidanceAction } from '@/composables/useGuidanceActions'
import { tAction, tServer, type ServerText } from '@/i18n/server'

interface IssueGroup extends ServerText {
  key: string
  severity: 'error' | 'warning' | 'info' | string
  title: string
  summary: string
  primary_action?: GuidanceAction
}

const { t } = useI18n()
const bridge = useBridgeStore()
const { run } = useGuidanceActions()
const busy = ref<string | null>(null)

/** The bridge's own guidance: what is wrong now, most important first. */
const issues = computed<IssueGroup[]>(() => {
  const g = (bridge.bridge as { guidance?: { issue_groups?: IssueGroup[] } } | null)?.guidance
  return (g?.issue_groups ?? []).slice(0, 3)
})

const icon = { error: XCircle, warning: AlertTriangle } as Record<string, typeof Info>
const tone: Record<string, string> = {
  error: 'border-error/40 bg-error/8',
  warning: 'border-warning/40 bg-warning/10',
}

async function act(issue: IssueGroup) {
  if (!issue.primary_action) return
  busy.value = issue.key
  try {
    await run(issue.primary_action)
  } finally {
    busy.value = null
  }
}
</script>

<template>
  <section v-if="issues.length" class="mb-6 space-y-2" :aria-label="t('guidance.title')">
    <div
      v-for="issue in issues"
      :key="issue.key"
      class="flex items-start gap-3 rounded-(--radius-card) border px-4 py-3"
      :class="tone[issue.severity] ?? 'border-info/40 bg-info/8'"
      :role="issue.severity === 'error' ? 'alert' : 'status'"
    >
      <component
        :is="icon[issue.severity] ?? Info"
        class="mt-0.5 size-5 shrink-0"
        :class="issue.severity === 'error' ? 'text-error' : issue.severity === 'warning' ? 'text-warning-text' : 'text-info'"
        aria-hidden="true"
      />
      <!-- Phones: the action goes under the text so the text keeps its width. -->
      <div data-guidance-body class="flex min-w-0 flex-1 flex-col items-start gap-2 sm:flex-row sm:items-start sm:gap-3">
        <div data-guidance-text class="min-w-0 sm:flex-1">
          <p class="text-sm font-medium text-text-primary">{{ tServer('issue', issue, 'title', issue.title) }}</p>
          <p class="mt-0.5 text-[13px] text-text-secondary">{{ tServer('issue', issue, 'summary', issue.summary) }}</p>
        </div>
        <SbButton
          v-if="issue.primary_action"
          variant="outline"
          size="sm"
          class="shrink-0"
          :loading="busy === issue.key"
          @click="act(issue)"
        >
          {{ tAction(issue.primary_action) }}
        </SbButton>
      </div>
    </div>
  </section>
</template>
