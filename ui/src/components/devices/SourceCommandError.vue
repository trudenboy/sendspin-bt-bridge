<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import type { SourceFailure } from '@/stores/source'

/**
 * The refusal the source store kept for this card's last command. The store
 * owns the failure per attempt; the card that attempted the switch shows it,
 * so the operator does not have to catch a toast that may already be gone.
 */
defineProps<{ failure: SourceFailure }>()

const { t } = useI18n()
</script>

<template>
  <p
    data-source-command-error
    role="alert"
    class="relative z-10 rounded-lg border border-error/40 bg-error/10 px-3 py-2 text-sm text-text-primary"
  >
    <span class="font-medium">{{ t('devices.sources.commandFailed') }}</span>
    <span v-if="failure.detail" class="mt-0.5 block text-text-secondary">{{ failure.detail }}</span>
    <span v-else class="mt-0.5 block font-mono text-xs text-text-secondary">{{ failure.code }}</span>
  </p>
</template>
