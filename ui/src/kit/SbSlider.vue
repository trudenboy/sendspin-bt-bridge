<script setup lang="ts">
import { computed } from 'vue'

interface Props {
  min?: number
  max?: number
  step?: number
  label?: string
  showValue?: boolean
  disabled?: boolean
  formatValue?: (v: number) => string
}

const props = withDefaults(defineProps<Props>(), {
  min: 0,
  max: 100,
  step: 1,
  showValue: true,
  disabled: false,
})

const model = defineModel<number>({ default: 0 })

const percentage = computed(() => {
  const range = props.max - props.min
  if (range === 0) return 0
  return ((model.value - props.min) / range) * 100
})

const displayValue = computed(() => {
  if (props.formatValue) return props.formatValue(model.value)
  return String(model.value)
})

function onInput(e: Event) {
  const target = e.target as HTMLInputElement
  model.value = Number(target.value)
}
</script>

<template>
  <div
    class="flex flex-col gap-1"
    :class="[disabled ? 'opacity-50' : '']"
  >
    <div v-if="label || showValue" class="flex items-center justify-between text-sm">
      <span v-if="label" class="font-medium text-text-primary dark:text-gray-200">
        {{ label }}
      </span>
      <span v-if="showValue" class="tabular-nums text-text-secondary dark:text-gray-400">
        {{ displayValue }}
      </span>
    </div>

    <input
      type="range"
      :value="model"
      :min="min"
      :max="max"
      :step="step"
      :disabled="disabled"
      class="sb-slider h-2 w-full cursor-pointer appearance-none rounded-full outline-none focus-visible:ring-2 focus-visible:ring-primary"
      :class="[disabled ? 'cursor-not-allowed' : '']"
      :style="{
        background: `linear-gradient(to right, var(--color-primary) ${percentage}%, var(--slider-track, #e5e7eb) ${percentage}%)`,
      }"
      @input="onInput"
    />
  </div>
</template>

<style scoped>
.sb-slider::-webkit-slider-thumb {
  -webkit-appearance: none;
  appearance: none;
  width: 1.25rem;
  height: 1.25rem;
  border-radius: 9999px;
  background: var(--color-primary);
  border: 2px solid white;
  box-shadow: 0 1px 3px rgb(0 0 0 / 0.2);
  cursor: pointer;
}

.sb-slider::-moz-range-thumb {
  width: 1.25rem;
  height: 1.25rem;
  border-radius: 9999px;
  background: var(--color-primary);
  border: 2px solid white;
  box-shadow: 0 1px 3px rgb(0 0 0 / 0.2);
  cursor: pointer;
}

:where(.dark, .dark *) .sb-slider {
  --slider-track: #374151;
}

:where(.dark, .dark *) .sb-slider::-webkit-slider-thumb {
  border-color: #1f2937;
}

:where(.dark, .dark *) .sb-slider::-moz-range-thumb {
  border-color: #1f2937;
}
</style>
