<script setup lang="ts">
import { computed, useId, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { Mic, TriangleAlert } from 'lucide-vue-next'
import { SbButton } from '@/kit'
import type { ListeningDevice } from '@/api/sync'
import type { SessionPoint, SyncMember } from '@/api/types'
import { inputKindKey } from '@/sync/listening'
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
  /** The bridge's inputs it could record from (empty: the bridge reports none, or cannot be asked). */
  devices?: ListeningDevice[]
}>()
const point = defineModel<SessionPoint>('point', { required: true })
/**
 * Record with this device's microphone, with a phone through the capture page (link + QR), or
 * with one of the bridge's own inputs (``input``: the bridge records the sequence itself).
 */
const capture = defineModel<'device' | 'phone' | 'input'>('capture', { default: 'device' })
/** The bridge input chosen when ``capture`` is ``input``. */
const inputId = defineModel<string>('inputId', { default: '' })
const emit = defineEmits<{ next: []; manual: []; skip: [member: string, skip: boolean] }>()

const { t } = useI18n()
const uid = useId()
const points = ['device', 'position'] as const
const devices = computed(() => props.devices ?? [])
const chosen = computed(() => devices.value.find((d) => d.id === inputId.value) ?? null)
const kindOf = (d: ListeningDevice) => t(inputKindKey(d))

/** Pick a bridge input (the shown names are its list); an unavailable one can never be chosen. */
function chooseInput(d: ListeningDevice) {
  if (!d.available) return
  capture.value = 'input'
  inputId.value = d.id
}

/*
 * Default the picker to the first input the bridge can actually record from; never change the
 * capture mode here (today's microphone-or-phone default stays).
 */
watch(
  devices,
  (list) => {
    if (!list.length) return
    if (list.some((d) => d.id === inputId.value)) return
    inputId.value = (list.find((d) => d.available) ?? list[0]!).id
  },
  { immediate: true },
)
/** About a minute per speaker (record, maybe repeat), plus two for getting the group ready. */
const minutes = computed(() => 2 + Math.max(1, props.count ?? 1))
</script>

<template>
  <div class="flex flex-col gap-4">
    <div
      role="note"
      class="flex gap-3 rounded-(--radius-card) border border-warning/50 bg-warning/10 px-4 py-3 text-sm text-text-primary"
    >
      <TriangleAlert class="mt-0.5 size-4 shrink-0 text-warning-text" aria-hidden="true" />
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
        :class="[
          'flex cursor-pointer gap-3 rounded-(--radius-card) border px-4 py-3 focus-within:ring-2 focus-within:ring-primary/40',
          capture === 'device' ? 'border-primary bg-primary/5' : 'border-border',
        ]"
      >
        <input
          v-model="capture"
          type="radio"
          :name="`${uid}-capture`"
          value="device"
          data-capture="device"
          :aria-describedby="`${uid}-device-capture-help`"
          class="mt-1 accent-primary"
        />
        <span class="flex flex-col">
          <span class="text-sm font-medium text-text-primary">{{ t('sync.wizard.capture.device') }}</span>
          <span :id="`${uid}-device-capture-help`" class="text-xs text-text-secondary">{{ t('sync.wizard.capture.deviceHelp') }}</span>
        </span>
      </label>
      <label
        v-for="d in devices"
        :key="d.id"
        :data-input-row="d.id"
        :class="[
          'flex gap-3 rounded-(--radius-card) border px-4 py-3 focus-within:ring-2 focus-within:ring-primary/40',
          d.available ? 'cursor-pointer' : 'cursor-not-allowed opacity-70',
          capture === 'input' && inputId === d.id ? 'border-primary bg-primary/5' : 'border-border',
        ]"
      >
        <input
          type="radio"
          :name="`${uid}-capture`"
          :value="`input:${d.id}`"
          :checked="capture === 'input' && inputId === d.id"
          :disabled="!d.available"
          data-capture="input"
          :data-listening-input="d.id"
          :data-input-unavailable="d.available ? undefined : d.id"
          class="mt-1 accent-primary"
          @change="chooseInput(d)"
        />
        <span class="flex flex-col">
          <span class="text-sm font-medium text-text-primary">{{ d.name }}</span>
          <span class="text-xs text-text-secondary">
            {{ kindOf(d) }}<template v-if="!d.available"> · {{ t('devices.sources.unavailable') }}</template>
          </span>
        </span>
      </label>
      <label
        :class="[
          'flex cursor-pointer gap-3 rounded-(--radius-card) border px-4 py-3 focus-within:ring-2 focus-within:ring-primary/40',
          capture === 'phone' ? 'border-primary bg-primary/5' : 'border-border',
        ]"
      >
        <input
          v-model="capture"
          type="radio"
          :name="`${uid}-capture`"
          value="phone"
          data-capture="phone"
          :aria-describedby="`${uid}-phone-capture-help`"
          class="mt-1 accent-primary"
        />
        <span class="flex flex-col">
          <span class="text-sm font-medium text-text-primary">{{ t('sync.wizard.capture.phone') }}</span>
          <span :id="`${uid}-phone-capture-help`" class="text-xs text-text-secondary">{{ t('sync.wizard.capture.phoneHelp') }}</span>
        </span>
      </label>
    </fieldset>

    <p v-if="capture === 'input' && chosen" data-input-help class="text-sm text-text-secondary">
      {{ t('sync.wizard.capture.inputHelp', { name: chosen.name }) }}
    </p>
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
      <SbButton
        type="button"
        data-next
        :loading="busy"
        :disabled="(!supported && capture === 'device') || (capture === 'input' && !chosen?.available)"
        @click="emit('next')"
      >
        {{ capture === 'device' ? t('sync.wizard.start') : t('sync.wizard.startPhone') }}
      </SbButton>
    </div>
  </div>
</template>
