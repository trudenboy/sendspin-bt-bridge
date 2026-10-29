<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { SbButton } from '@/kit'
import type { AirCalibrationCandidate, SyncSession } from '@/api/types'
import { saveAirCalibrations } from '@/api/sync'
import { errorCode, useSyncStore } from '@/stores/sync'
import { speakerName } from '@/sync/names'

/**
 * A bridge speaker's air-to-sound latency from the last phone sequence (the air sensor): eligible
 * members with the value, the speaker's own delay report as a hint and a "save" checkbox; the others
 * with the reason in words. The value needs a reference member whose sound is on schedule.
 */
const props = defineProps<{ session: SyncSession }>()

const { t } = useI18n()
const store = useSyncStore()

const MIN_MS = 0
const MAX_MS = 2000
const candidates = computed<AirCalibrationCandidate[]>(() => props.session.air_calibrations ?? [])
const members = computed(() => props.session.members ?? [])
const nameOf = (id: string) => speakerName(members.value.find((m) => m.ma_player_id === id)?.name, id)
const saveable = (c: AirCalibrationCandidate) =>
  c.eligible && c.latency_ms != null && c.latency_ms >= MIN_MS && c.latency_ms <= MAX_MS

const chosen = ref<Record<string, boolean>>({})
const saved = ref<Record<string, boolean>>({})
const busy = ref(false)
const error = ref('')
const picked = computed(() => candidates.value.filter((c) => saveable(c) && chosen.value[c.member]).map((c) => c.member))

async function save() {
  if (!picked.value.length) return
  busy.value = true
  error.value = ''
  try {
    const done = await saveAirCalibrations(props.session.id, picked.value)
    for (const c of done) saved.value[c.member] = true
    chosen.value = {}
  } catch (e) {
    error.value = t('sync.air.saveFailed', { code: errorCode(e) })
  } finally {
    busy.value = false
  }
}

async function pickReference(event: Event) {
  const member = (event.target as HTMLSelectElement).value
  if (!member) return
  error.value = ''
  try {
    await store.reference(member)
  } catch (e) {
    error.value = t('sync.air.referenceFailed', { code: errorCode(e) })
  }
}
</script>

<template>
  <section
    v-if="candidates.length"
    data-air-calibrations
    class="flex flex-col gap-3 rounded-(--radius-card) border border-border-default px-4 py-3"
  >
    <h3 class="text-sm font-medium text-text-primary">{{ t('sync.air.title') }}</h3>
    <p class="text-xs text-text-secondary">{{ t('sync.air.help') }}</p>

    <div class="flex flex-wrap items-center gap-2">
      <label for="sync-air-reference" class="text-sm text-text-primary">{{ t('sync.air.reference') }}</label>
      <select
        id="sync-air-reference"
        data-air-reference
        :value="session.reference ?? ''"
        class="rounded-(--radius-input) border border-border-strong bg-surface-card px-2 py-1 text-sm text-text-primary"
        @change="pickReference"
      >
        <option value="" disabled>{{ t('sync.air.referencePick') }}</option>
        <option v-for="m in members" :key="m.ma_player_id" :value="m.ma_player_id">{{ speakerName(m.name, m.ma_player_id) }}</option>
      </select>
    </div>
    <p data-air-reference-hint class="text-xs text-text-secondary">{{ t('sync.air.referenceHint') }}</p>

    <ul class="flex flex-col gap-2">
      <li
        v-for="c in candidates"
        :key="c.member"
        :data-air-row="c.member"
        class="flex flex-wrap items-center justify-between gap-2 text-sm"
      >
        <span class="font-medium text-text-primary">{{ nameOf(c.member) }}</span>
        <template v-if="c.eligible && c.latency_ms != null">
          <span class="text-text-primary">
            {{ t('sync.air.value', { ms: Math.round(c.latency_ms) }) }}
            <span v-if="c.delay_report_ms != null" class="text-xs text-text-secondary">
              · {{ t('sync.air.hint', { ms: Math.round(c.delay_report_ms) }) }}
            </span>
          </span>
          <span v-if="saved[c.member]" class="text-xs text-success">{{ t('sync.air.saved') }}</span>
          <label v-else-if="saveable(c)" class="flex items-center gap-2 text-text-primary">
            <input v-model="chosen[c.member]" type="checkbox" :data-air-save="c.member" />
            {{ t('sync.air.save') }}
          </label>
          <span v-else class="text-xs text-warning-text">{{ t('sync.air.outOfRange', { min: MIN_MS, max: MAX_MS }) }}</span>
        </template>
        <span v-else class="text-text-secondary">{{ t(`sync.air.reasons.${c.reason ?? 'not_effective'}`) }}</span>
      </li>
    </ul>

    <p v-if="error" data-air-error role="alert" class="text-sm text-error">{{ error }}</p>
    <div v-if="candidates.some(saveable)" class="flex justify-end">
      <SbButton
        type="button"
        variant="outline"
        data-air-save-button
        :disabled="!picked.length || busy"
        :loading="busy"
        @click="save"
      >
        {{ t('sync.air.saveButton') }}
      </SbButton>
    </div>
  </section>
</template>
