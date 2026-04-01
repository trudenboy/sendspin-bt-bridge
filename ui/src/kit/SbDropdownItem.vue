<script setup lang="ts">
interface Props {
  disabled?: boolean
  destructive?: boolean
}

withDefaults(defineProps<Props>(), {
  disabled: false,
  destructive: false,
})

const emit = defineEmits<{
  click: []
}>()

function onClick() {
  emit('click')
}
</script>

<template>
  <button
    type="button"
    role="menuitem"
    :disabled="disabled"
    class="flex w-full items-center px-4 py-2 text-left text-sm transition-colors"
    :class="[
      disabled
        ? 'opacity-50 cursor-not-allowed text-text-disabled'
        : destructive
          ? 'text-error hover:bg-red-50 dark:hover:bg-red-900/20 cursor-pointer'
          : 'text-text-primary hover:bg-surface-secondary dark:text-gray-200 dark:hover:bg-gray-700 cursor-pointer',
    ]"
    @click="onClick"
  >
    <slot />
  </button>
</template>
