<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { SbDialog } from '@/kit'
import { SHORTCUTS, shortcutsOpen } from '@/composables/useKeyboardShortcuts'

const { t } = useI18n()
</script>

<template>
  <SbDialog v-model="shortcutsOpen" :title="t('shortcuts.title')" size="sm">
    <dl class="grid grid-cols-[auto_1fr] items-center gap-x-6 gap-y-3 text-sm">
      <template v-for="s in SHORTCUTS" :key="s.action">
        <dt class="flex gap-1">
          <kbd
            v-for="k in s.keys"
            :key="k"
            class="min-w-6 rounded border border-border-strong bg-surface-secondary px-1.5 py-0.5 text-center font-mono text-xs text-text-primary"
          >{{ k }}</kbd>
        </dt>
        <dd class="text-text-secondary">{{ t(`shortcuts.${s.action}`) }}</dd>
      </template>
    </dl>
  </SbDialog>
</template>
