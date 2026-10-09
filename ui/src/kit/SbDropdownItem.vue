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
          ? 'text-error hover:bg-error/10 cursor-pointer'
          : 'text-text-primary hover:bg-surface-secondary cursor-pointer',
    ]"
    @click="onClick"
  >
    <slot />
  </button>
</template>
