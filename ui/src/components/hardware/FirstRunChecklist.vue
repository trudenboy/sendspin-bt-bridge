<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { Check, ChevronRight } from 'lucide-vue-next'
import { useBridgeStore } from '@/stores/bridge'

/**
 * The three things a new install needs, in the order they make sound.  It is
 * advisory, not a wizard: every row links to the control that does the work,
 * a completed step ticks itself off, and the whole card disappears once the
 * bridge can play — or when the operator hides it.
 */
const { t } = useI18n()
const bridge = useBridgeStore()
const emit = defineEmits<{ addSpeaker: [] }>()

const DISMISS_KEY = 'sendspin-ui:first-run-dismissed'
const dismissed = ref(localStorage.getItem(DISMISS_KEY) === '1')


type Step = { id: string; done: boolean; label: string; action: string; href?: string }
const steps = computed<Step[]>(() => [
  { id: 'speaker', done: bridge.devices.length > 0, label: t('home.checklist.speaker'), action: t('home.checklist.speakerAction') },
  { id: 'ma', done: bridge.maConnected, label: t('home.checklist.ma'), action: t('home.checklist.maAction'), href: '/config#settings-musicAssistant' },
])
const doneCount = computed(() => steps.value.filter((step) => step.done).length)
const show = computed(() => !dismissed.value && !bridge.loading && !!bridge.bridge && doneCount.value < steps.value.length)

function dismiss() {
  dismissed.value = true
  try {
    localStorage.setItem(DISMISS_KEY, '1')
  } catch {
    /* private mode: hidden for this visit */
  }
}
</script>

<template>
  <section
    v-if="show"
    data-first-run
    class="mb-4 rounded-(--radius-card) border border-border bg-surface-card px-4 py-3"
    :aria-label="t('home.checklist.title')"
  >
    <div class="flex items-start justify-between gap-3">
      <div class="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
        <h2 class="whitespace-nowrap text-sm font-semibold text-text-primary">{{ t('home.checklist.title') }}</h2>
        <p data-first-run-progress class="whitespace-nowrap text-xs text-text-secondary">
          {{ t('home.checklist.progress', { done: doneCount, total: steps.length }) }}
        </p>
      </div>
      <button
        type="button"
        data-first-run-dismiss
        class="shrink-0 rounded-(--radius-button) px-2 py-1 text-xs text-text-secondary hover:bg-surface-secondary hover:text-text-primary"
        :aria-label="t('home.checklist.dismiss')"
        @click="dismiss"
      >
        {{ t('home.checklist.dismiss') }}
      </button>
    </div>

    <ul class="mt-2 divide-y divide-border">
      <li
        v-for="step in steps"
        :key="step.id"
        :data-step="step.id"
        :data-done="String(step.done)"
        class="flex flex-wrap items-center gap-x-3 gap-y-1 py-2 text-sm"
      >
        <span
          class="inline-flex size-5 shrink-0 items-center justify-center rounded-full border"
          :class="step.done ? 'border-success bg-success/10 text-success' : 'border-border-strong text-text-tertiary'"
          aria-hidden="true"
        >
          <Check v-if="step.done" class="size-3" />
        </span>
        <span class="min-w-0 flex-1" :class="step.done ? 'text-text-secondary line-through' : 'text-text-primary'">
          {{ step.label }}
        </span>
        <button
          v-if="step.id === 'speaker'"
          type="button"
          class="inline-flex items-center gap-0.5 text-sm text-primary-text underline underline-offset-2 max-sm:basis-full max-sm:pl-8"
          @click="emit('addSpeaker')"
        >
          {{ step.action }}
          <ChevronRight class="size-3.5" aria-hidden="true" />
        </button>
        <RouterLink
          v-else
          :to="step.href!"
          class="inline-flex items-center gap-0.5 text-sm text-primary-text underline underline-offset-2 max-sm:basis-full max-sm:pl-8"
        >
          {{ step.action }}
          <ChevronRight class="size-3.5" aria-hidden="true" />
        </RouterLink>
      </li>
    </ul>
  </section>
</template>
