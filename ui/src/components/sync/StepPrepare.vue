<script setup lang="ts">
import { computed, useId } from 'vue'
import { useI18n } from 'vue-i18n'
import { Mic, TriangleAlert } from 'lucide-vue-next'
import { SbButton } from '@/kit'
import type { SessionPoint, SyncMember } from '@/api/types'
import ReadinessWarnings from './ReadinessWarnings.vue'

const props = defineProps<{
  /** Starting: the microphone prompt or the session start is on its way. */
  busy: boolean
  /** Speakers that will be measured (for the time estimate). */
  count?: number
  /** The group's members as last read (what would stop a measurement is said before it starts). */
  members?: SyncMember[]
  /** Speakers the operator chose to skip. */
  skipped?: string[]
  /** Why the last start failed, already translated. */
  error: string
  /** Capture works in this browser at all. */
  supported: boolean
}>()
const point = defineModel<SessionPoint>('point', { required: true })
/** Record with this device's microphone, or with a phone through the capture page (link + QR). */
const capture = defineModel<'device' | 'phone'>('capture', { default: 'device' })
const emit = defineEmits<{ next: []; manual: []; skip: [member: string, skip: boolean] }>()

const { t } = useI18n()
const uid = useId()
const points = ['device', 'position'] as const
const captures = ['device', 'phone'] as const
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

    <ReadinessWarnings
      :members="members ?? []"
      :skipped="skipped"
      @skip="(id, on) => emit('skip', id, on)"
      @manual="emit('manual')"
    />

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

    <fieldset class="flex flex-col gap-2">
      <legend class="mb-2 text-sm font-medium text-text-primary">{{ t('sync.wizard.capture.legend') }}</legend>
      <label
        v-for="c in captures"
        :key="c"
        :class="[
          'flex cursor-pointer gap-3 rounded-(--radius-card) border px-4 py-3 focus-within:ring-2 focus-within:ring-primary/40',
          capture === c ? 'border-primary bg-primary/5' : 'border-border',
        ]"
      >
        <input
          v-model="capture"
          type="radio"
          :name="`${uid}-capture`"
          :value="c"
          :data-capture="c"
          :aria-describedby="`${uid}-${c}-capture-help`"
          class="mt-1 accent-primary"
        />
        <span class="flex flex-col">
          <span class="text-sm font-medium text-text-primary">{{ t(`sync.wizard.capture.${c}`) }}</span>
          <span :id="`${uid}-${c}-capture-help`" class="text-xs text-text-secondary">{{ t(`sync.wizard.capture.${c}Help`) }}</span>
        </span>
      </label>
    </fieldset>

    <p v-if="capture === 'device'" class="flex gap-2 text-sm text-text-secondary">
      <Mic class="mt-0.5 size-4 shrink-0" aria-hidden="true" />{{ t('sync.wizard.micPermission') }}
    </p>
    <p class="text-sm text-text-secondary">{{ t('sync.wizard.holdStill') }}</p>
    <p data-estimate class="text-sm text-text-secondary">{{ t('sync.wizard.estimate', { min: minutes }) }}</p>

    <div v-if="(!supported && capture === 'device') || error" role="alert" class="flex flex-col gap-2 text-sm">
      <p class="text-error">{{ error || t('sync.capture.errors.unsupported') }}</p>
      <p v-if="capture === 'device'" class="text-text-secondary">
        <button type="button" data-use-phone class="font-medium text-primary underline underline-offset-2" @click="capture = 'phone'">
          {{ t('sync.wizard.capture.usePhone') }}
        </button>
      </p>
      <p class="text-text-secondary">
        {{ t('sync.wizard.byEar') }}
        <button type="button" data-to-manual class="font-medium text-primary underline underline-offset-2" @click="emit('manual')">
          {{ t('sync.actions.manual') }}
        </button>
      </p>
    </div>

    <div class="flex justify-end">
      <SbButton type="button" data-next :loading="busy" :disabled="!supported && capture === 'device'" @click="emit('next')">
        {{ capture === 'phone' ? t('sync.wizard.startPhone') : t('sync.wizard.start') }}
      </SbButton>
    </div>
  </div>
</template>
