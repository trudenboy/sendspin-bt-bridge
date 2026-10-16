<script setup lang="ts">
import { computed, ref, useId, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { Play, Square } from 'lucide-vue-next'
import { SbButton } from '@/kit'
import type { SyncMember } from '@/api/types'

const props = defineProps<{
  members: SyncMember[]
  signalRunning: boolean
  /** The bridge sees the clicker playing on the group: solo and A/B only then. */
  soloReady?: boolean
  soloed: string | null
  /** The pair taking turns, while A/B runs. */
  alternating?: readonly [string, string] | null
}>()

const emit = defineEmits<{
  signal: [action: 'start' | 'stop']
  solo: [member: string | null]
  ab: [a: string, b: string]
}>()

const { t } = useI18n()
const uid = useId()
const ready = computed(() => props.signalRunning && (props.soloReady ?? true))

/** The A/B pair, defaulting to the first two members (and kept valid as members come and go). */
const a = ref('')
const b = ref('')
watch(
  () => props.members.map((m) => m.ma_player_id),
  (ids) => {
    if (!ids.includes(a.value)) a.value = ids.find((id) => id !== b.value) ?? ''
    if (!ids.includes(b.value) || b.value === a.value) b.value = ids.find((id) => id !== a.value) ?? ''
  },
  { immediate: true },
)

const selectClass =
  'h-8 min-w-0 flex-1 rounded-(--radius-input) border border-border-strong bg-surface-card px-2 text-sm text-text-primary outline-none focus:ring-2 focus:ring-primary/40'
</script>

<template>
  <div class="flex flex-col gap-3">
    <SbButton
      type="button"
      data-signal
      :variant="signalRunning ? 'outline' : 'primary'"
      :aria-pressed="signalRunning"
      @click="emit('signal', signalRunning ? 'stop' : 'start')"
    >
      <template #icon-left>
        <Square v-if="signalRunning" aria-hidden="true" />
        <Play v-else aria-hidden="true" />
      </template>
      {{ t(signalRunning ? 'sync.listen.stop' : 'sync.listen.play') }}
    </SbButton>
    <p data-play-help class="-mt-2 text-xs text-text-secondary">{{ t('sync.listen.playHelp') }}</p>

    <div role="group" :aria-label="t('sync.listen.solo')" class="flex flex-wrap gap-1">
      <SbButton
        type="button"
        size="sm"
        data-solo-none
        :variant="soloed == null && !alternating ? 'primary' : 'secondary'"
        :aria-pressed="soloed == null && !alternating"
        @click="emit('solo', null)"
      >
        {{ t('sync.listen.everyone') }}
      </SbButton>
      <SbButton
        v-for="m in members"
        :key="m.ma_player_id"
        type="button"
        size="sm"
        :data-solo="m.ma_player_id"
        :variant="soloed === m.ma_player_id && !alternating ? 'primary' : 'secondary'"
        :aria-pressed="soloed === m.ma_player_id && !alternating"
        :aria-label="t('sync.listen.soloMember', { name: m.name })"
        :disabled="!ready"
        @click="emit('solo', m.ma_player_id)"
      >
        {{ m.name }}
      </SbButton>
    </div>

    <div v-if="members.length >= 2" class="flex flex-col gap-1">
      <div class="flex items-center gap-2">
        <label :for="`${uid}-a`" class="text-sm text-text-secondary">{{ t('sync.listen.abA') }}</label>
        <select :id="`${uid}-a`" v-model="a" data-ab-a :class="selectClass">
          <option v-for="m in members" :key="m.ma_player_id" :value="m.ma_player_id" :disabled="m.ma_player_id === b">
            {{ m.name }}
          </option>
        </select>
        <label :for="`${uid}-b`" class="text-sm text-text-secondary">{{ t('sync.listen.abB') }}</label>
        <select :id="`${uid}-b`" v-model="b" data-ab-b :class="selectClass">
          <option v-for="m in members" :key="m.ma_player_id" :value="m.ma_player_id" :disabled="m.ma_player_id === a">
            {{ m.name }}
          </option>
        </select>
        <SbButton
          type="button"
          size="sm"
          data-ab
          :variant="alternating ? 'primary' : 'secondary'"
          :aria-pressed="!!alternating"
          :aria-describedby="`${uid}-ab-help`"
          :disabled="!ready || !a || !b || a === b"
          @click="emit('ab', a, b)"
        >
          {{ t('sync.listen.ab') }}
        </SbButton>
      </div>
      <p :id="`${uid}-ab-help`" class="text-xs text-text-secondary">
        {{ t('sync.listen.abHelp') }}<template v-if="!signalRunning"> {{ t('sync.listen.needsClicks') }}</template>
      </p>
    </div>
  </div>
</template>
