<script setup lang="ts">
import { useId } from 'vue'
import { useI18n } from 'vue-i18n'
import { Mic, TriangleAlert } from 'lucide-vue-next'
import { SbButton } from '@/kit'
import type { SessionPoint } from '@/api/types'

defineProps<{
  /** Starting: the microphone prompt or the session start is on its way. */
  busy: boolean
  /** Why the last start failed, already translated. */
  error: string
  /** Capture works in this browser at all. */
  supported: boolean
}>()
const point = defineModel<SessionPoint>('point', { required: true })
const emit = defineEmits<{ next: [] }>()

const { t } = useI18n()
const uid = useId()
const points = ['device', 'position'] as const
</script>

<template>
  <div class="flex flex-col gap-4">
    <div
      role="note"
      class="flex gap-3 rounded-(--radius-card) border border-warning/50 bg-warning/10 px-4 py-3 text-sm text-text-primary"
    >
      <TriangleAlert class="mt-0.5 size-4 shrink-0 text-warning" aria-hidden="true" />
      <div>
        <p class="font-medium">{{ t('sync.wizard.interrupted') }}</p>
        <p class="text-text-secondary">{{ t('sync.wizard.interruptedHelp') }}</p>
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

    <p v-if="!supported" role="alert" class="text-sm text-error">{{ t('sync.capture.errors.unsupported') }}</p>
    <p v-else-if="error" role="alert" class="text-sm text-error">{{ error }}</p>

    <div class="flex justify-end">
      <SbButton type="button" data-next :loading="busy" :disabled="!supported" @click="emit('next')">
        {{ t('sync.wizard.next') }}
      </SbButton>
    </div>
  </div>
</template>
