<script setup lang="ts">
import { ref, computed } from 'vue'

const props = withDefaults(
  defineProps<{
    content: string
    position?: 'top' | 'bottom' | 'left' | 'right'
    delay?: number
  }>(),
  { position: 'top', delay: 300 },
)

const visible = ref(false)
let timer: ReturnType<typeof setTimeout> | null = null
const tooltipId = `sb-tooltip-${Math.random().toString(36).slice(2, 9)}`

function show() {
  timer = setTimeout(() => {
    visible.value = true
  }, props.delay)
}

function hide() {
  if (timer) {
    clearTimeout(timer)
    timer = null
  }
  visible.value = false
}

const positionClasses = computed(() => {
  const map = {
    top: 'bottom-full left-1/2 -translate-x-1/2 mb-2',
    bottom: 'top-full left-1/2 -translate-x-1/2 mt-2',
    left: 'right-full top-1/2 -translate-y-1/2 mr-2',
    right: 'left-full top-1/2 -translate-y-1/2 ml-2',
  } as const
  return map[props.position]
})

const enterTransform = computed(() => {
  const map = {
    top: 'translate(-50%, 4px)',
    bottom: 'translate(-50%, -4px)',
    left: 'translate(4px, -50%)',
    right: 'translate(-4px, -50%)',
  } as const
  return map[props.position]
})
</script>

<template>
  <span
    class="relative inline-flex"
    @mouseenter="show"
    @mouseleave="hide"
    @focusin="show"
    @focusout="hide"
  >
    <span :aria-describedby="visible ? tooltipId : undefined">
      <slot />
    </span>
    <Transition name="sb-tooltip">
      <span
        v-if="visible"
        :id="tooltipId"
        role="tooltip"
        :class="[
          'pointer-events-none absolute z-50 whitespace-nowrap rounded-md bg-code-bg px-2 py-1 text-xs text-code-text shadow-lg',
          positionClasses,
        ]"
      >
        {{ content }}
      </span>
    </Transition>
  </span>
</template>

<style scoped>
.sb-tooltip-enter-active,
.sb-tooltip-leave-active {
  transition:
    opacity 150ms ease,
    transform 150ms ease;
}

.sb-tooltip-enter-from,
.sb-tooltip-leave-to {
  opacity: 0;
  transform: v-bind(enterTransform);
}
</style>
