<script setup lang="ts">
import { computed } from 'vue'
import SbSpinner from './SbSpinner.vue'

const props = withDefaults(
  defineProps<{
    variant?: 'primary' | 'secondary' | 'ghost' | 'danger' | 'warning'
    size?: 'sm' | 'md' | 'lg'
    loading?: boolean
    disabled?: boolean
    icon?: boolean
  }>(),
  { variant: 'primary' as const, size: 'md' as const, loading: false, disabled: false, icon: false },
)

const variantClasses = {
  primary: 'bg-primary text-white hover:bg-primary-dark',
  secondary: 'bg-surface-secondary text-text-primary hover:opacity-80',
  ghost: 'bg-transparent text-text-primary hover:bg-surface-secondary',
  danger: 'bg-error text-white hover:bg-red-700',
  warning: 'bg-warning text-white hover:opacity-90',
} as const

const sizeClasses = computed(() => {
  if (props.icon) {
    const map = { sm: 'h-8 w-8', md: 'h-10 w-10', lg: 'h-12 w-12' } as const
    return map[props.size]
  }
  const map = {
    sm: 'h-8 px-3 text-sm',
    md: 'h-10 px-4 text-base',
    lg: 'h-12 px-6 text-lg',
  } as const
  return map[props.size]
})

const isDisabled = computed(() => props.disabled || props.loading)
</script>

<template>
  <button
    :class="[
      'inline-flex cursor-pointer items-center justify-center gap-2 rounded-[--radius-button] font-medium transition-all focus:outline-none focus-visible:ring-2 focus-visible:ring-primary',
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
