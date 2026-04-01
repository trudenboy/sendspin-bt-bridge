<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { SbBadge, SbStatusDot } from '@/kit'

const props = defineProps<{
  state: string
}>()

const { t } = useI18n()

type Tone = 'success' | 'warning' | 'error' | 'info' | 'neutral'
type DotStatus = 'streaming' | 'ready' | 'connecting' | 'error' | 'offline' | 'standby'

const toneMap: Record<string, Tone> = {
  STREAMING: 'success',
  READY: 'info',
  CONNECTING: 'warning',
  ERROR: 'error',
  OFFLINE: 'neutral',
  STANDBY: 'neutral',
  IDLE: 'neutral',
}

const dotStatusMap: Record<string, DotStatus> = {
  STREAMING: 'streaming',
  READY: 'ready',
  CONNECTING: 'connecting',
  ERROR: 'error',
  OFFLINE: 'offline',
  STANDBY: 'standby',
  IDLE: 'offline',
}

const i18nKeyMap: Record<string, string> = {
  STREAMING: 'streaming',
  READY: 'ready',
  CONNECTING: 'connecting',
  ERROR: 'error',
  OFFLINE: 'offline',
  STANDBY: 'standby',
  IDLE: 'idle',
}

const tone = computed<Tone>(() => toneMap[props.state] ?? 'neutral')
const dotStatus = computed<DotStatus>(() => dotStatusMap[props.state] ?? 'offline')
const label = computed(() => {
  const key = i18nKeyMap[props.state]
  return key ? t(`device.status.${key}`) : props.state
})
</script>

<template>
  <SbBadge :tone="tone" size="sm">
    <SbStatusDot :status="dotStatus" size="sm" />
    {{ label }}
  </SbBadge>
</template>
