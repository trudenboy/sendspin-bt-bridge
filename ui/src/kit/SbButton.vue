<script setup lang="ts">
import { computed } from 'vue'
import SbSpinner from './SbSpinner.vue'

const props = withDefaults(
  defineProps<{
    variant?: 'primary' | 'outline' | 'secondary' | 'ghost' | 'danger' | 'warning'
    size?: 'sm' | 'md' | 'lg'
    loading?: boolean
    disabled?: boolean
    icon?: boolean
  }>(),
  { variant: 'primary' as const, size: 'md' as const, loading: false, disabled: false, icon: false },
)

// Shapes and sizes follow Music Assistant's buttons (shadcn-vue "new-york"):
// 36 px default height, 14 px label, 8 px radius.
const variantClasses = {
  primary: 'bg-primary-fill text-on-primary hover:bg-primary-dark',
  outline: 'border border-border-strong bg-transparent text-text-primary hover:bg-surface-secondary',
  secondary: 'bg-surface-secondary text-text-primary hover:brightness-95 dark:hover:brightness-125',
  ghost: 'bg-transparent text-text-primary hover:bg-surface-secondary',
  danger: 'bg-error-fill text-on-primary hover:brightness-90',
  warning: 'bg-warning text-black/85 hover:brightness-95',
} as const

const sizeClasses = computed(() => {
  if (props.icon) {
    const map = { sm: 'h-8 w-8', md: 'h-9 w-9', lg: 'h-10 w-10' } as const
    return map[props.size]
  }
  const map = {
    sm: 'h-8 px-3 text-sm',
    md: 'h-9 px-4 text-sm',
    lg: 'h-10 px-6 text-base',
  } as const
  return map[props.size]
})

const isDisabled = computed(() => props.disabled || props.loading)
</script>

<template>
  <button
    :class="[
      'inline-flex shrink-0 cursor-pointer items-center justify-center gap-2 whitespace-nowrap rounded-(--radius-button) font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/50 focus-visible:ring-offset-1 focus-visible:ring-offset-surface [&_svg]:size-4 [&_svg]:shrink-0',
      variantClasses[variant],
      sizeClasses,
      isDisabled && 'pointer-events-none opacity-50 cursor-not-allowed',
    ]"
    :disabled="isDisabled"
  >
    <SbSpinner v-if="loading" size="sm" label="Loading" />
    <slot v-if="!loading" name="icon-left" />
    <slot />
    <slot v-if="!loading" name="icon-right" />
  </button>
</template>
