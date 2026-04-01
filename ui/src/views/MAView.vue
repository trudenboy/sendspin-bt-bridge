<script setup lang="ts">
import { useI18n } from 'vue-i18n'

const { t } = useI18n()
</script>

<template>
  <div>
    <h1 class="mb-6 text-2xl font-bold text-text-primary">
      {{ t('app.ma') }}
    </h1>
    <p class="text-text-secondary">
      Music Assistant integration — coming soon.
    </p>
  </div>
</template>
