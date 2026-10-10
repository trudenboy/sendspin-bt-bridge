<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { SbBadge, SbStatusDot } from '@/kit'

/** A device state as derived by ``deviceState()`` (health states plus standby/disabled). */
const props = defineProps<{
  state: string
}>()

const { t } = useI18n()

type Tone = 'success' | 'warning' | 'error' | 'info' | 'neutral'
type DotStatus = 'online' | 'streaming' | 'ready' | 'connecting' | 'error' | 'offline' | 'standby'

const STATES: Record<string, { tone: Tone; dot: DotStatus }> = {
  streaming: { tone: 'success', dot: 'streaming' },
  ready: { tone: 'neutral', dot: 'online' },
  transitioning: { tone: 'warning', dot: 'connecting' },
  recovering: { tone: 'warning', dot: 'connecting' },
  degraded: { tone: 'error', dot: 'error' },
  offline: { tone: 'neutral', dot: 'offline' },
  standby: { tone: 'neutral', dot: 'standby' },
  released: { tone: 'warning', dot: 'standby' },
  disabled: { tone: 'neutral', dot: 'offline' },
}

const look = computed(() => STATES[props.state] ?? { tone: 'neutral' as Tone, dot: 'offline' as DotStatus })
const label = computed(() => (STATES[props.state] ? t(`device.status.${props.state}`) : props.state))
</script>

<template>
  <SbBadge :tone="look.tone" size="sm">
    <SbStatusDot :status="look.dot" size="sm" :label="label" />
    {{ label }}
  </SbBadge>
</template>
