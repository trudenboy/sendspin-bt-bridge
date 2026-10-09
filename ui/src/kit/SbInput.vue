<script setup lang="ts">
import { computed, useId } from 'vue'

interface Props {
  label?: string
  placeholder?: string
  type?: 'text' | 'password' | 'email' | 'number' | 'url'
  error?: string
  hint?: string
  disabled?: boolean
  required?: boolean
  id?: string
}

const props = withDefaults(defineProps<Props>(), {
  type: 'text',
  disabled: false,
  required: false,
})

const model = defineModel<string>({ default: '' })

const autoId = `sb-input-${useId()}`
const inputId = computed(() => props.id ?? autoId)
const descriptionId = computed(() => `${inputId.value}-desc`)
const hasDescription = computed(() => !!props.error || !!props.hint)
</script>

<template>
  <div class="flex flex-col gap-1">
    <label
      v-if="label"
      :for="inputId"
      class="text-sm font-medium text-text-primary dark:text-gray-200"
    >
      {{ label }}
      <span v-if="required" class="text-error ml-0.5">*</span>
    </label>

    <div
      class="flex items-center rounded-[--radius-input] border bg-surface-card transition-colors dark:bg-gray-800"
      :class="[
        error
          ? 'border-error focus-within:ring-2 focus-within:ring-error/40'
          : 'border-gray-300 focus-within:ring-2 focus-within:ring-primary/40 dark:border-gray-600',
        disabled ? 'opacity-50 cursor-not-allowed' : '',
      ]"
    >
      <span v-if="$slots.prefix" class="pl-3 text-text-secondary">
        <slot name="prefix" />
      </span>

      <input
        :id="inputId"
        v-model="model"
        :type="type"
        :placeholder="placeholder"
        :disabled="disabled"
        :required="required"
        :aria-invalid="error ? true : undefined"
        :aria-describedby="hasDescription ? descriptionId : undefined"
        :aria-required="required || undefined"
        class="w-full bg-transparent px-3 py-2 text-sm text-text-primary outline-none placeholder:text-text-disabled dark:text-white dark:placeholder:text-gray-500"
        :class="[disabled ? 'cursor-not-allowed' : '']"
      />

      <span v-if="$slots.suffix" class="pr-3 text-text-secondary">
        <slot name="suffix" />
      </span>
    </div>

    <p
      v-if="error"
      :id="descriptionId"
      class="text-xs text-error"
      role="alert"
    >
      {{ error }}
    </p>
    <p
      v-else-if="hint"
      :id="descriptionId"
      class="text-xs text-text-secondary"
    >
      {{ hint }}
    </p>
  </div>
</template>
