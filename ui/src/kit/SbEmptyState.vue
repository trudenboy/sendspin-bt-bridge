<script setup lang="ts">
withDefaults(
  defineProps<{
    title: string
    description?: string
    icon?: string
  }>(),
  {
    description: undefined,
    icon: 'default',
  },
)
</script>

<template>
  <div class="flex flex-col items-center justify-center px-4 py-12 text-center">
    <!-- Icon -->
    <div class="mb-4 text-5xl text-text-disabled">
      <slot name="icon">
        <svg
          xmlns="http://www.w3.org/2000/svg"
          class="h-16 w-16"
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
          stroke-width="1.5"
          aria-hidden="true"
        >
          <path
            stroke-linecap="round"
            stroke-linejoin="round"
            d="M20 7l-8-4-8 4m16 0l-8 4m8-4v10l-8 4m0-10L4 7m8 4v10M4 7v10l8 4"
          />
        </svg>
      </slot>
    </div>

    <!-- Title -->
    <h3 class="mb-2 text-lg font-semibold text-text-primary">
      {{ title }}
    </h3>

    <!-- Description -->
    <p
      v-if="description"
      class="mb-6 max-w-md text-text-secondary"
    >
      {{ description }}
    </p>

    <!-- Content slot -->
    <slot />

    <!-- Action slot -->
    <div v-if="$slots.action" class="mt-4">
      <slot name="action" />
    </div>
  </div>
</template>
