<script setup lang="ts">
import { useNotificationStore } from '@/stores/notifications'
import SbToast from './SbToast.vue'

const notifications = useNotificationStore()
</script>

<template>
  <div class="fixed right-4 bottom-4 z-[100] flex flex-col-reverse gap-2">
    <TransitionGroup
      name="sb-toast"
      tag="div"
      class="flex flex-col-reverse gap-2"
    >
      <SbToast
        v-for="toast in notifications.toasts"
        :key="toast.id"
        :id="toast.id"
        :message="toast.message"
        :type="toast.type"
        :duration="toast.duration"
        @close="notifications.remove($event)"
      />
    </TransitionGroup>
  </div>
</template>

<style scoped>
.sb-toast-enter-active {
  transition: all 0.3s ease;
}
.sb-toast-leave-active {
  transition: all 0.2s ease;
}
.sb-toast-enter-from {
  opacity: 0;
  transform: translateX(100%);
}
.sb-toast-leave-to {
  opacity: 0;
  transform: translateX(100%);
}
</style>
