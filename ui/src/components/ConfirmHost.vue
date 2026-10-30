<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { ConfirmConsequences } from '@/kit'
import { confirmState, settleConfirm } from '@/composables/useConfirm'

const { t } = useI18n()
</script>

<template>
  <ConfirmConsequences
    :model-value="confirmState.open"
    :title="confirmState.options?.title"
    :message="confirmState.options?.message"
    :consequences="confirmState.options?.consequences"
    :link="confirmState.options?.link"
    :danger="confirmState.options?.danger"
    :confirm-label="confirmState.options?.confirmLabel ?? t('common.confirm')"
    :cancel-label="confirmState.options?.cancelLabel ?? t('common.cancel')"
    @confirm="settleConfirm(true)"
    @cancel="settleConfirm(false)"
  />
</template>
