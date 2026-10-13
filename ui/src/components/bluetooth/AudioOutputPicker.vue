<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useAudioStore } from '@/stores/audio'
import { ApiError } from '@/api/client'
import { SbBadge, SbButton, SbInput, SbSpinner } from '@/kit'
import { CheckCircle2, RotateCw, Volume2 } from 'lucide-vue-next'
import type { AudioSink } from '@/api/types'

/**
 * Adding a player that plays to an audio output of this computer: choose the
 * output, name it, done. Outputs another player already uses are listed but
 * cannot be chosen.
 */
const emit = defineEmits<{ stage: [value: 'pick' | 'name' | 'done']; close: [] }>()

const { t } = useI18n()
const audio = useAudioStore()

const stage = ref<'pick' | 'name' | 'done'>('pick')
const chosen = ref<AudioSink | null>(null)
const name = ref('')
const room = ref('')
const error = ref('')
const saving = ref(false)

const label = (s: AudioSink) => (s.description && s.description !== s.name ? s.description : s.name)

watch(stage, (s) => emit('stage', s))
onMounted(() => void audio.fetchSinks())

function pick(sink: AudioSink) {
  chosen.value = sink
  name.value = label(sink)
  error.value = ''
  stage.value = 'name'
}

function back() {
  stage.value = 'pick'
  chosen.value = null
  error.value = ''
}

const finalName = computed(() => name.value.trim() || (chosen.value ? label(chosen.value) : ''))

async function add() {
  if (!chosen.value || saving.value) return
  saving.value = true
  error.value = ''
  try {
    if (await audio.addAudioOutput(chosen.value.name, finalName.value, { room: room.value })) stage.value = 'done'
    else error.value = t('addSpeaker.audio.taken')
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : String(e)
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div v-if="stage === 'pick'" class="space-y-4">
    <div class="flex items-start gap-3">
      <p class="flex-1 text-sm text-text-secondary">{{ t('addSpeaker.audio.intro') }}</p>
      <SbButton variant="outline" size="sm" :loading="audio.loading" @click="audio.fetchSinks()">
        <template #icon-left><RotateCw class="size-4" /></template>
        {{ t('addSpeaker.audio.refresh') }}
      </SbButton>
    </div>

    <div v-if="audio.loading && !audio.sinks.length" class="flex flex-col items-center gap-2 py-8 text-sm text-text-secondary">
      <SbSpinner size="md" />
      {{ t('addSpeaker.audio.looking') }}
    </div>

    <ul v-else-if="audio.sinks.length" class="divide-y divide-border rounded-(--radius-card) border border-border">
      <li v-for="s in audio.sinks" :key="s.name" class="flex items-center gap-3 px-3 py-2.5" :class="s.in_use_by ? 'opacity-60' : ''">
        <Volume2 class="size-5 shrink-0 text-text-secondary" aria-hidden="true" />
        <div class="min-w-0 flex-1">
          <p class="truncate text-sm font-medium text-text-primary">{{ label(s) }}</p>
          <p class="truncate font-mono text-xs text-text-tertiary">{{ s.name }}</p>
          <p v-if="s.in_use_by" class="text-xs text-text-secondary">{{ t('addSpeaker.audio.inUse') }}</p>
        </div>
        <SbBadge v-if="s.state === 'running'" tone="success" size="sm">{{ s.state }}</SbBadge>
        <SbButton size="sm" :disabled="!!s.in_use_by" @click="pick(s)">{{ t('addSpeaker.add') }}</SbButton>
      </li>
    </ul>

    <p v-else class="py-6 text-center text-sm text-text-secondary">{{ audio.error ?? t('addSpeaker.audio.none') }}</p>
  </div>

  <form v-else-if="stage === 'name'" class="space-y-4" @submit.prevent="add">
    <p class="text-sm text-text-secondary">{{ t('addSpeaker.audio.named') }}</p>
    <SbInput v-model="name" :label="t('settings.device.name.label')" :hint="t('settings.device.name.help')" />
    <SbInput v-model="room" :label="t('settings.device.room_name.label')" :hint="t('addSpeaker.roomHint')" />
    <div class="flex justify-end gap-2">
      <SbButton variant="ghost" type="button" @click="back">{{ t('common.cancel') }}</SbButton>
      <SbButton type="button" :loading="saving" @click="add">{{ t('addSpeaker.addToBridge') }}</SbButton>
    </div>
  </form>

  <div v-else class="space-y-4">
    <p class="flex items-center gap-2 text-sm text-text-primary">
      <CheckCircle2 class="size-5 text-success" aria-hidden="true" />
      {{ t('addSpeaker.addedTitle', { name: finalName }) }}
    </p>
    <div class="flex justify-end">
      <SbButton @click="emit('close')">{{ t('addSpeaker.done') }}</SbButton>
    </div>
  </div>

  <p v-if="error" class="mt-3 text-sm text-error" role="alert">{{ error }}</p>
</template>
