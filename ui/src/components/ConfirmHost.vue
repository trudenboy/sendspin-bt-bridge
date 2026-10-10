<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { SbButton, SbDialog } from '@/kit'
import { confirmState, settleConfirm } from '@/composables/useConfirm'

const { t } = useI18n()
</script>

<template>
  <SbDialog
    :model-value="confirmState.open"
    :title="confirmState.options?.title"
    size="sm"
    @update:model-value="(open: unknown) => !open && settleConfirm(false)"
  >
    <p v-if="confirmState.options?.message" class="text-sm text-text-secondary">{{ confirmState.options.message }}</p>
    <template #footer>
      <SbButton variant="ghost" @click="settleConfirm(false)">{{ confirmState.options?.cancelLabel ?? t('common.cancel') }}</SbButton>
      <SbButton :variant="confirmState.options?.danger ? 'danger' : 'primary'" @click="settleConfirm(true)">
        {{ confirmState.options?.confirmLabel ?? t('common.confirm') }}
      </SbButton>
    </template>
  </SbDialog>
</template>
