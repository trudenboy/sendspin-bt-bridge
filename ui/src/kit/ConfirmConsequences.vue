<script setup lang="ts">
import SbButton from './SbButton.vue'
import SbDialog from './SbDialog.vue'

/**
 * The bridge's confirm dialog for disruptive or irreversible actions: a title,
 * what the action does in one list (which speakers, whether playback stops),
 * an optional side link (for example "Download current config first") and a
 * button named for the action. Texts come from the caller; see POLICY.md.
 */
defineProps<{
  modelValue: boolean
  title?: string
  message?: string
  /** One line per consequence; name the speakers and say whether playback stops. */
  consequences?: string[]
  confirmLabel: string
  cancelLabel: string
  danger?: boolean
  link?: { label: string; run: () => void }
}>()

const emit = defineEmits<{ confirm: []; cancel: [] }>()
</script>

<template>
  <SbDialog
    :model-value="modelValue"
    :title="title"
    size="sm"
    @update:model-value="(open: unknown) => !open && emit('cancel')"
  >
    <p v-if="message" class="text-sm text-text-secondary">{{ message }}</p>
    <ul v-if="consequences?.length" class="mt-2 list-disc space-y-1 pl-5 text-sm text-text-primary" data-testid="consequences">
      <li v-for="(line, i) in consequences" :key="i" data-testid="consequence">{{ line }}</li>
    </ul>
    <button
      v-if="link"
      type="button"
      class="mt-3 cursor-pointer text-sm text-primary-text underline-offset-2 hover:underline"
      data-testid="confirm-link"
      @click="link.run()"
    >
      {{ link.label }}
    </button>
    <template #footer>
      <SbButton variant="ghost" data-testid="confirm-no" @click="emit('cancel')">{{ cancelLabel }}</SbButton>
      <SbButton :variant="danger ? 'danger' : 'primary'" data-testid="confirm-yes" @click="emit('confirm')">{{ confirmLabel }}</SbButton>
    </template>
  </SbDialog>
</template>
