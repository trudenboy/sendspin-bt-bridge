<script setup lang="ts">
import { computed, useId } from 'vue'
import { useI18n } from 'vue-i18n'
import { Mic, TriangleAlert } from 'lucide-vue-next'
import { SbButton } from '@/kit'
import type { SessionPoint } from '@/api/types'

const props = defineProps<{
  /** Starting: the microphone prompt or the session start is on its way. */
  busy: boolean
  /** Speakers that will be measured (for the time estimate). */
  count?: number
  /** Why the last start failed, already translated. */
  error: string
  /** Capture works in this browser at all. */
  supported: boolean
}>()
const point = defineModel<SessionPoint>('point', { required: true })
const emit = defineEmits<{ next: []; manual: [] }>()

const { t } = useI18n()
const uid = useId()
const points = ['device', 'position'] as const
/** About a minute per speaker (record, maybe repeat), plus two for getting the group ready. */
const minutes = computed(() => 2 + Math.max(1, props.count ?? 1))
</script>

<template>
  <div class="flex flex-col gap-4">
    <div
      role="note"
      class="flex gap-3 rounded-(--radius-card) border border-warning/50 bg-warning/10 px-4 py-3 text-sm text-text-primary"
    >
      <TriangleAlert class="mt-0.5 size-4 shrink-0 text-warning" aria-hidden="true" />
      <div>
        <p class="font-medium">{{ t('sync.wizard.notice.title') }}</p>
        <p class="text-text-secondary">{{ t('sync.wizard.notice.help') }}</p>
        <p data-queue-note class="mt-1 text-text-secondary">{{ t('sync.wizard.notice.queue') }}</p>
      </div>
    </div>

    <fieldset class="flex flex-col gap-2">
      <legend class="mb-2 text-sm font-medium text-text-primary">{{ t('sync.wizard.point.legend') }}</legend>
      <label
        v-for="p in points"
        :key="p"
        :class="[
          'flex cursor-pointer gap-3 rounded-(--radius-card) border px-4 py-3 focus-within:ring-2 focus-within:ring-primary/40',
          point === p ? 'border-primary bg-primary/5' : 'border-border',
        ]"
      >
        <input
          v-model="point"
          type="radio"
          :name="`${uid}-point`"
          :value="p"
          :aria-describedby="`${uid}-${p}-help`"
          class="mt-1 accent-primary"
        />
        <span class="flex flex-col">
          <span class="text-sm font-medium text-text-primary">{{ t(`sync.wizard.point.${p}`) }}</span>
          <span :id="`${uid}-${p}-help`" class="text-xs text-text-secondary">{{ t(`sync.wizard.point.${p}Help`) }}</span>
        </span>
      </label>
    </fieldset>

    <p class="flex gap-2 text-sm text-text-secondary">
      <Mic class="mt-0.5 size-4 shrink-0" aria-hidden="true" />{{ t('sync.wizard.micPermission') }}
    </p>
    <p class="text-sm text-text-secondary">{{ t('sync.wizard.holdStill') }}</p>
    <p data-estimate class="text-sm text-text-secondary">{{ t('sync.wizard.estimate', { min: minutes }) }}</p>

    <div v-if="!supported || error" role="alert" class="flex flex-col gap-2 text-sm">
      <p class="text-error">{{ !supported ? t('sync.capture.errors.unsupported') : error }}</p>
      <p class="text-text-secondary">
        {{ t('sync.wizard.byEar') }}
        <button type="button" data-to-manual class="font-medium text-primary underline underline-offset-2" @click="emit('manual')">
          {{ t('sync.actions.manual') }}
        </button>
      </p>
    </div>

    <div class="flex justify-end">
      <SbButton type="button" data-next :loading="busy" :disabled="!supported" @click="emit('next')">
        {{ t('sync.wizard.start') }}
      </SbButton>
    </div>
  </div>
</template>
