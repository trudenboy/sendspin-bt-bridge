<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { AlertTriangle } from 'lucide-vue-next'
import { SbBadge, SbButton, SbDialog } from '@/kit'
import type { SyncProposal, SyncSession } from '@/api/types'
import { formatDelta } from '@/sync/format'
import SpreadChart from './SpreadChart.vue'

/**
 * Step 4: the proposal. Before → after (Δ) per speaker, the spread before and after, the
 * reference, warnings, and the actions. When Music Assistant refused the write
 * (``session.error === 'ma_forbidden'``) the values are offered to copy instead.
 */
const props = defineProps<{
  session: SyncSession
  proposal: SyncProposal
  mode: 'semi' | 'auto'
  /** An apply (or save) is under way. */
  busy?: boolean
  /** A failure to show (already translated). */
  error?: string
}>()
const emit = defineEmits<{
  /** ``rows`` only when some proposed members left the group (they are left out); ``verify``: check again. */
  apply: [rows: Record<string, number> | undefined, verify: boolean]
  applyAndSave: [name: string, rows: Record<string, number> | undefined, verify: boolean]
  reference: [member: string]
  cancel: []
}>()

const { t, locale } = useI18n()

const LARGE_MS = 2000
const departed = computed(() => props.session.departed ?? [])
const nameOf = (id: string) =>
  props.session.members?.find((m) => m.ma_player_id === id)?.name ?? id
const isGone = (id: string) => departed.value.includes(id)

const rows = computed(() => props.proposal.rows)
const live = computed(() => rows.value.filter((r) => !isGone(r.member)))

/** Members that left the group are left out of the write (Music Assistant would refuse them). */
const applyRows = computed<Record<string, number> | undefined>(() =>
  live.value.length === rows.value.length ? undefined : Object.fromEntries(live.value.map((r) => [r.member, r.after_ms])),
)

const history = computed(() => {
  const h = props.session.history ?? []
  return (h[h.length - 1] ?? null) as { spread_before_ms?: number; spread_after_ms?: number } | null
})
const spreadBefore = computed(() => history.value?.spread_before_ms ?? null)
const measuredAfter = computed(() => history.value?.spread_after_ms ?? null)

/** Warnings in the operator's language, derived from the rows (the bridge's own text is English). */
const warnings = computed(() => {
  const out: string[] = []
  if (rows.value.some((r) => r.clamped)) out.push(t('sync.proposal.clampedWarning', { ms: props.proposal.predicted_spread_ms }))
  if (rows.value.some((r) => r.after_ms > LARGE_MS)) out.push(t('sync.proposal.largeWarning'))
  const known = /^(A delay above|Some delays were clamped)/
  for (const w of props.proposal.warnings ?? []) if (!known.test(w)) out.push(w)
  return out
})

const unverified = computed(() => (props.proposal.unverified ?? []).map(nameOf))

/* -- reference ----------------------------------------------------------------------- */
const reference = computed(() => props.session.reference ?? props.proposal.reference)
function onReference(e: Event) {
  const id = (e.target as HTMLSelectElement).value
  if (id && id !== reference.value) emit('reference', id)
}

/* -- Music Assistant refused: the values to set by hand ----------------------------------- */
const forbidden = computed(() => props.session.error === 'ma_forbidden')
const copyText = computed(() => live.value.map((r) => `${nameOf(r.member)}: ${r.after_ms} ms`).join('\n'))
const copyState = ref<'' | 'copied' | 'failed'>('')
async function copyValues() {
  try {
    await navigator.clipboard.writeText(copyText.value)
    copyState.value = 'copied'
  } catch {
    copyState.value = 'failed'
  }
}

/* -- actions ------------------------------------------------------------------------- */
const checkAgain = ref(false)
const verify = computed(() => props.mode === 'semi' && checkAgain.value)

function apply() {
  emit('apply', applyRows.value, verify.value)
}

const saveOpen = ref(false)
const profileName = ref('')
const nameMissing = ref(false)
function askName() {
  const date = new Intl.DateTimeFormat(locale.value, { dateStyle: 'short', timeStyle: 'short' }).format(new Date())
  profileName.value = t('sync.proposal.defaultName', { date })
  nameMissing.value = false
  saveOpen.value = true
}
function confirmSave() {
  const name = profileName.value.trim()
  if (!name) {
    nameMissing.value = true
    return
  }
  saveOpen.value = false
  emit('applyAndSave', name, applyRows.value, verify.value)
}
</script>

<template>
  <div class="flex flex-col gap-4">
    <p class="text-sm text-text-secondary">{{ t('sync.proposal.help') }}</p>

    <div class="flex flex-col gap-1">
      <label for="sync-proposal-reference" class="text-sm font-medium text-text-primary">{{ t('sync.proposal.reference') }}</label>
      <select
        id="sync-proposal-reference"
        data-reference
        :value="reference"
        :disabled="busy"
        aria-describedby="sync-proposal-reference-help"
        class="max-w-xs rounded-(--radius-input) border border-border-strong bg-surface-card px-3 py-2 text-sm text-text-primary outline-none focus:ring-2 focus:ring-primary/40"
        @change="onReference"
      >
        <option v-for="r in live" :key="r.member" :value="r.member">{{ nameOf(r.member) }}</option>
      </select>
      <p id="sync-proposal-reference-help" class="text-xs text-text-secondary">{{ t('sync.proposal.referenceHelp') }}</p>
    </div>

    <div class="overflow-x-auto">
      <table class="w-full text-sm">
        <caption class="sr-only">{{ t('sync.proposal.caption') }}</caption>
        <thead>
          <tr class="border-b border-border text-left text-text-secondary">
            <th scope="col" class="py-2 pr-3 font-medium">{{ t('sync.proposal.member') }}</th>
            <th scope="col" class="py-2 pr-3 text-right font-medium">{{ t('sync.proposal.before') }}</th>
            <th scope="col" class="py-2 pr-3 text-right font-medium">{{ t('sync.proposal.after') }}</th>
            <th scope="col" class="py-2 text-right font-medium">{{ t('sync.proposal.change') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in rows" :key="r.member" :data-row="r.member" class="border-b border-border last:border-0">
            <th scope="row" class="py-2 pr-3 text-left font-medium text-text-primary">
              {{ nameOf(r.member) }}
              <SbBadge v-if="isGone(r.member)" tone="neutral" size="sm" class="ml-1">{{ t('sync.proposal.departed') }}</SbBadge>
            </th>
            <td class="py-2 pr-3 text-right tabular-nums text-text-secondary">{{ t('sync.proposal.ms', { ms: r.before_ms }) }}</td>
            <td class="py-2 pr-3 text-right tabular-nums text-text-primary">
              <span class="inline-flex items-center gap-1">
                <AlertTriangle
                  v-if="r.clamped"
                  data-clamped
                  class="size-4 text-warning"
                  role="img"
                  :aria-label="t('sync.proposal.clamped')"
                />
                {{ t('sync.proposal.ms', { ms: r.after_ms }) }}
              </span>
            </td>
            <td class="py-2 text-right tabular-nums text-text-primary">{{ t('sync.proposal.ms', { ms: formatDelta(r.delta_ms) }) }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <SpreadChart
      :before="spreadBefore"
      :after="measuredAfter ?? proposal.predicted_spread_ms"
      :predicted="measuredAfter == null"
    />
    <p class="text-sm text-text-primary">{{ t('sync.proposal.predicted', { ms: proposal.predicted_spread_ms }) }}</p>

    <div
      v-if="warnings.length"
      data-warnings
      class="rounded-(--radius-card) border border-warning/50 bg-warning/10 px-4 py-3 text-sm text-text-primary"
    >
      <p class="mb-1 flex items-center gap-2 font-medium">
        <AlertTriangle class="size-4 text-warning" aria-hidden="true" />{{ t('sync.proposal.warnings') }}
      </p>
      <ul class="list-disc pl-5">
        <li v-for="w in warnings" :key="w">{{ w }}</li>
      </ul>
    </div>

    <p v-if="unverified.length" data-unverified class="text-sm text-text-secondary">
      {{ t('sync.proposal.unverified', { names: unverified.join(', ') }) }}
    </p>

    <div
      v-if="forbidden"
      data-forbidden
      role="alert"
      class="flex flex-col gap-2 rounded-(--radius-card) border border-error/50 bg-error/10 px-4 py-3 text-sm text-text-primary"
    >
      <p>{{ t('sync.proposal.forbidden') }}</p>
      <pre data-copy-text class="whitespace-pre-wrap rounded bg-surface-card px-3 py-2 font-mono text-sm select-all">{{ copyText }}</pre>
      <div class="flex flex-wrap items-center gap-3">
        <SbButton type="button" size="sm" variant="outline" data-copy-values @click="copyValues">
          {{ t('sync.proposal.copy') }}
        </SbButton>
        <span v-if="copyState" role="status" class="text-text-secondary">
          {{ copyState === 'copied' ? t('sync.proposal.copied') : t('sync.proposal.copyFailed') }}
        </span>
      </div>
      <p class="text-text-secondary">{{ t('sync.proposal.forbiddenHint') }}</p>
    </div>

    <p v-if="error" data-apply-error role="alert" class="text-sm text-error">{{ error }}</p>

    <label v-if="mode === 'semi'" class="flex items-start gap-2 text-sm text-text-primary">
      <input v-model="checkAgain" data-check-again type="checkbox" class="mt-0.5 size-4 accent-primary" :disabled="busy" />
      <span>
        {{ t('sync.proposal.checkAgain') }}
        <span class="block text-xs text-text-secondary">{{ t('sync.proposal.checkAgainHelp') }}</span>
      </span>
    </label>

    <div class="flex flex-wrap justify-end gap-3">
      <SbButton type="button" variant="outline" data-cancel :disabled="busy" @click="emit('cancel')">
        {{ t('sync.proposal.cancel') }}
      </SbButton>
      <SbButton type="button" variant="outline" data-apply-save :disabled="busy || !live.length" @click="askName">
        {{ t('sync.proposal.applySave') }}
      </SbButton>
      <SbButton type="button" data-apply :loading="busy" :disabled="!live.length" @click="apply">
        {{ t('sync.proposal.apply') }}
      </SbButton>
    </div>

    <SbDialog v-model="saveOpen" :title="t('sync.proposal.saveTitle')">
      <div class="flex flex-col gap-3">
        <div class="flex flex-col gap-1">
          <label for="sync-proposal-profile-name" class="text-sm font-medium text-text-primary">{{ t('sync.proposal.name') }}</label>
          <input
            id="sync-proposal-profile-name"
            v-model="profileName"
            data-profile-name
            type="text"
            required
            maxlength="100"
            :aria-invalid="nameMissing || undefined"
            :aria-describedby="nameMissing ? 'sync-proposal-profile-name-error' : undefined"
            :class="[
              'rounded-(--radius-input) border border-border-strong bg-surface-card px-3 py-2 text-sm text-text-primary outline-none focus:ring-2 focus:ring-primary/40',
              nameMissing && 'border-error',
            ]"
            @input="nameMissing = false"
            @keydown.enter.prevent="confirmSave"
          />
          <p v-if="nameMissing" id="sync-proposal-profile-name-error" role="alert" class="text-xs text-error">
            {{ t('sync.proposal.nameRequired') }}
          </p>
        </div>
        <div class="flex justify-end gap-3">
          <SbButton type="button" variant="outline" @click="saveOpen = false">{{ t('sync.proposal.cancel') }}</SbButton>
          <SbButton type="button" data-save-confirm @click="confirmSave">{{ t('sync.proposal.save') }}</SbButton>
        </div>
      </div>
    </SbDialog>
  </div>
</template>
