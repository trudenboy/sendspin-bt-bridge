<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useBridgeStore } from '@/stores/bridge'
import { getSourceTargets, setSourceTarget } from '@/api/source'
import type { SourceTarget } from '@/api/types'
import SettingsRow from './SettingsRow.vue'

/**
 * Pin the live input to one Music Assistant queue.  "Choose in Music
 * Assistant" is the default and keeps MA's own routing in charge; choosing a
 * queue here makes the bridge start the source there when a device begins
 * streaming.  Without an MA API token the selector stays visible but disabled
 * with the reason and a link, rather than hiding the capability.
 */
const { t } = useI18n()
const bridge = useBridgeStore()

const source = computed(() => bridge.bridge?.sendspin_source ?? null)
const enabled = computed(() => !!source.value?.enabled)
const apiAvailable = ref(true)
const targets = ref<SourceTarget[]>([])
const selected = ref(source.value?.target_player ?? '')
const loading = ref(false)
const saving = ref(false)
const error = ref<string | null>(null)

const groups = computed(() => targets.value.filter((target) => target.type === 'group'))
const players = computed(() => targets.value.filter((target) => target.type === 'player'))
const targetError = computed(() => source.value?.target_error ?? null)

async function load() {
  if (!enabled.value) return
  loading.value = true
  error.value = null
  try {
    const answer = await getSourceTargets()
    targets.value = answer.targets ?? []
    apiAvailable.value = answer.ma_api_available !== false
    selected.value = answer.selected ?? source.value?.target_player ?? ''
    if (answer.error) error.value = t('settings.parts.sourceTarget.loadFailed')
  } catch {
    error.value = t('settings.parts.sourceTarget.loadFailed')
  } finally {
    loading.value = false
  }
}

async function save(value: string) {
  saving.value = true
  error.value = null
  try {
    const answer = (await setSourceTarget(value || null)) as {
      target_player?: string | null
      target_error?: string | null
    }
    selected.value = answer.target_player ?? value
    if (answer.target_error) error.value = t('settings.parts.sourceTarget.playFailed')
    await bridge.refresh()
  } catch {
    error.value = t('settings.parts.sourceTarget.saveFailed')
  } finally {
    saving.value = false
  }
}

function onChange(event: Event) {
  void save((event.target as HTMLSelectElement).value)
}

watch(
  () => [enabled.value, source.value?.ma_paired] as const,
  ([on]) => {
    if (on) void load()
  },
  { immediate: true },
)

watch(
  () => source.value?.target_player,
  (value) => {
    if (!saving.value) selected.value = value ?? ''
  },
  { immediate: true },
)
</script>

<template>
  <div v-if="enabled" data-source-target>
    <SettingsRow :label="t('settings.parts.sourceTarget.label')" :help="t('settings.parts.sourceTarget.help')">
      <select
        data-source-target-select
        class="w-full rounded-(--radius-input) border border-border-strong bg-surface-card px-3 py-2 text-sm text-text-primary focus:border-primary disabled:cursor-not-allowed disabled:opacity-50"
        :value="selected"
        :disabled="loading || saving || !apiAvailable"
        :aria-label="t('settings.parts.sourceTarget.label')"
        @change="onChange"
      >
        <option value="">{{ t('settings.parts.sourceTarget.default') }}</option>
        <optgroup v-if="groups.length" :label="t('settings.parts.sourceTarget.groups')">
          <option v-for="target in groups" :key="target.player_id" :value="target.player_id" :disabled="!target.available">
            {{ target.name }}{{ target.available ? '' : ` — ${t('settings.parts.sourceTarget.unavailable')}` }}
          </option>
        </optgroup>
        <optgroup v-if="players.length" :label="t('settings.parts.sourceTarget.players')">
          <option v-for="target in players" :key="target.player_id" :value="target.player_id" :disabled="!target.available">
            {{ target.name }}{{ target.available ? '' : ` — ${t('settings.parts.sourceTarget.unavailable')}` }}
          </option>
        </optgroup>
      </select>
    </SettingsRow>

    <p v-if="!apiAvailable" data-source-target-api class="mb-4 text-sm text-text-secondary">
      {{ t('settings.parts.sourceTarget.apiUnavailable') }}
      <a href="#settings-musicAssistant" class="text-primary-text underline-offset-2 hover:underline">
        {{ t('settings.parts.sourceTarget.apiUnavailableAction') }}
      </a>
    </p>
    <p v-else-if="loading" class="mb-4 text-sm text-text-secondary">{{ t('settings.parts.sourceTarget.loading') }}</p>
    <p v-else-if="error" data-source-target-error role="alert" class="mb-4 text-sm text-error">
      {{ error }}
      <a href="#settings-musicAssistant" class="text-primary-text underline-offset-2 hover:underline">
        {{ t('settings.parts.sourceTarget.apiUnavailableAction') }}
      </a>
    </p>
    <p v-else-if="targetError" data-source-target-error role="alert" class="mb-4 text-sm text-error">
      {{ t('settings.parts.sourceTarget.playFailed') }}
      <span class="font-mono text-xs">{{ targetError }}</span>
    </p>
    <p v-else class="mb-4 text-xs text-text-secondary">{{ t('settings.parts.sourceTarget.note') }}</p>
  </div>
</template>
