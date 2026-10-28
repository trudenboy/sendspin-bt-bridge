<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { AlertTriangle } from 'lucide-vue-next'
import { SbButton, SbDialog } from '@/kit'
import type { SyncProposal, SyncSession } from '@/api/types'
import { formatMs } from '@/sync/format'
import { speakerName } from '@/sync/names'
import AirCalibrations from './AirCalibrations.vue'
import ManualTuning from './ManualTuning.vue'

/**
 * Stage 3 of the measurement (semi-automatic): the measured delays are applied and the session is the
 * manual editor — the same listening (solo, A/B) and delay rows as Manual tuning, with what the
 * measurement found next to each field ("Reset to measured"). Members that were not heard or were
 * skipped keep their delay. Finish keeps the delays; Cancel puts back the ones from before.
 */
const props = defineProps<{
  session: SyncSession
  proposal: SyncProposal
  /** Finishing (or cancelling) is under way. */
  busy?: boolean
  /** A failure to show (already translated). */
  error?: string
}>()
const emit = defineEmits<{
  finish: [profileName: string | null]
  cancel: []
}>()

const { t, locale } = useI18n()

const LARGE_MS = 2000
const departed = computed(() => props.session.departed ?? [])
const rows = computed(() => props.proposal.rows.filter((r) => !departed.value.includes(r.member)))
const measured = computed(() => Object.fromEntries(rows.value.map((r) => [r.member, r.after_ms])))
const before = computed(() => Object.fromEntries(props.proposal.rows.map((r) => [r.member, r.before_ms])))
const nameOf = (id: string) => speakerName(props.session.members?.find((m) => m.ma_player_id === id)?.name, id)

const spreadBefore = computed(() => {
  const h = props.session.history ?? []
  const last = (h[h.length - 1] ?? null) as { spread_before_ms?: number } | null
  return last?.spread_before_ms ?? null
})

/** Warnings in the operator's language, from the structured fields. */
const warnings = computed(() => {
  const out: string[] = []
  if (rows.value.some((r) => r.clamped)) out.push(t('sync.proposal.clampedWarning', { ms: props.proposal.predicted_spread_ms }))
  if (rows.value.some((r) => r.after_ms > LARGE_MS)) out.push(t('sync.proposal.largeWarning'))
  return out
})
const kept = computed(() => (props.proposal.unverified ?? []).filter((id) => !departed.value.includes(id)).map(nameOf))

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
  emit('finish', name)
}
</script>

<template>
  <div data-tune class="flex flex-col gap-4">
    <p class="text-sm text-text-primary">{{ t('sync.tune.help') }}</p>
    <p v-if="spreadBefore != null" data-spread class="text-sm text-text-secondary">
      {{ t('sync.tune.spread', { before: formatMs(spreadBefore), after: formatMs(proposal.predicted_spread_ms) }) }}
    </p>
    <p v-if="kept.length" data-kept class="text-sm text-text-secondary">
      {{ t('sync.tune.kept', { names: kept.join(', ') }) }}
    </p>
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

    <ManualTuning skip-prep :measured="measured" :before="before" />

    <AirCalibrations :session="session" />

    <p v-if="error" data-tune-error role="alert" class="text-sm text-error">{{ error }}</p>
    <div class="flex flex-wrap justify-end gap-3">
      <SbButton type="button" variant="outline" data-cancel :disabled="busy" @click="emit('cancel')">
        {{ t('sync.wizard.cancel') }}
      </SbButton>
      <SbButton type="button" variant="outline" data-finish-save :disabled="busy" @click="askName">
        {{ t('sync.tune.finishSave') }}
      </SbButton>
      <SbButton type="button" data-finish :loading="busy" @click="emit('finish', null)">
        {{ t('sync.tune.finish') }}
      </SbButton>
    </div>

    <SbDialog v-model="saveOpen" :title="t('sync.tune.saveTitle')">
      <div class="flex flex-col gap-3">
        <div class="flex flex-col gap-1">
          <label for="sync-tune-profile-name" class="text-sm font-medium text-text-primary">{{ t('sync.proposal.name') }}</label>
          <input
            id="sync-tune-profile-name"
            v-model="profileName"
            data-profile-name
            type="text"
            required
            maxlength="100"
            :aria-invalid="nameMissing || undefined"
            :aria-describedby="nameMissing ? 'sync-tune-profile-name-error' : undefined"
            :class="[
              'rounded-(--radius-input) border border-border-strong bg-surface-card px-3 py-2 text-sm text-text-primary outline-none focus:ring-2 focus:ring-primary/40',
              nameMissing && 'border-error',
            ]"
            @input="nameMissing = false"
            @keydown.enter.prevent="confirmSave"
          />
          <p v-if="nameMissing" id="sync-tune-profile-name-error" role="alert" class="text-xs text-error">
            {{ t('sync.proposal.nameRequired') }}
          </p>
        </div>
        <div class="flex justify-end gap-3">
          <SbButton type="button" variant="outline" @click="saveOpen = false">{{ t('sync.profiles.cancel') }}</SbButton>
          <SbButton type="button" data-save-confirm @click="confirmSave">{{ t('sync.tune.saveConfirm') }}</SbButton>
        </div>
      </div>
    </SbDialog>
  </div>
</template>
