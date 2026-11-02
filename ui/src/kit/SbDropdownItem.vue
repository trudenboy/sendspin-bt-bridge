<script setup lang="ts">
interface Props {
  disabled?: boolean
  destructive?: boolean
  /** A plain action, a radio choice (language, theme) or a checkbox (technical details). */
  role?: 'menuitem' | 'menuitemradio' | 'menuitemcheckbox'
  checked?: boolean
}

withDefaults(defineProps<Props>(), {
  disabled: false,
  destructive: false,
  role: 'menuitem',
  checked: false,
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
    :role="role"
    :aria-checked="role === 'menuitem' ? undefined : checked"
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
