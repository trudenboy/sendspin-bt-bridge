<script setup lang="ts">
import { computed, useId } from 'vue'

interface Props {
  label?: string
  disabled?: boolean
  size?: 'sm' | 'md'
  id?: string
}

const props = withDefaults(defineProps<Props>(), {
  size: 'md',
  disabled: false,
})

const model = defineModel<boolean>({ default: false })

const toggleId = computed(() => props.id ?? useId())

function toggle() {
  if (!props.disabled) {
    model.value = !model.value
  }
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === ' ' || e.key === 'Enter') {
    e.preventDefault()
    toggle()
  }
}
</script>

<template>
  <div
    class="inline-flex items-center gap-2"
    :class="[disabled ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer']"
    @click="toggle"
  >
    <button
      :id="toggleId"
      type="button"
      role="switch"
      :aria-checked="model"
      :aria-label="label"
      :disabled="disabled"
      class="relative inline-flex shrink-0 rounded-full border-2 border-transparent transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2"
      :class="[
        model
          ? 'bg-primary'
          : 'bg-gray-300 dark:bg-gray-600',
        size === 'sm' ? 'h-5 w-8' : 'h-6 w-10',
      ]"
      @click.stop="toggle"
      @keydown="onKeydown"
    >
      <span
        class="pointer-events-none inline-block transform rounded-full bg-white shadow-sm ring-0 transition-transform"
        :class="[
          size === 'sm' ? 'h-4 w-4' : 'h-5 w-5',
          model
            ? size === 'sm' ? 'translate-x-3' : 'translate-x-4'
            : 'translate-x-0',
        ]"
      />
    </button>

    <label
      v-if="label"
      :for="toggleId"
      class="text-sm text-text-primary select-none dark:text-gray-200"
      :class="[disabled ? 'cursor-not-allowed' : 'cursor-pointer']"
      @click.prevent
    >
      {{ label }}
    </label>
  </div>
</template>
