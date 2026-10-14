<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { SbBadge, SbStatusDot } from '@/kit'
import { useNow } from '@/composables/useNow'
import { releaseKind, standbyElapsedMinutes } from '@/utils/deviceStatusText'
import type { Device } from '@/api/types'

/**
 * A device state as derived by ``deviceState()`` (health states plus runtime
 * states). With the device it also says how long standby has lasted and
 * whether a release was automatic.
 */
const props = defineProps<{
  state: string
  device?: Device
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
  released: { tone: 'neutral', dot: 'standby' },
  output_missing: { tone: 'error', dot: 'error' },
  disabled: { tone: 'neutral', dot: 'offline' },
  stopping: { tone: 'warning', dot: 'connecting' },
  reconnecting: { tone: 'warning', dot: 'connecting' },
  buffering: { tone: 'warning', dot: 'connecting' },
  sink_muted: { tone: 'warning', dot: 'error' },
}

const now = useNow()
const autoReleased = computed(() => props.state === 'released' && !!props.device && releaseKind(props.device) === 'auto')

const look = computed(() => {
  if (autoReleased.value) return { tone: 'warning' as Tone, dot: 'standby' as DotStatus }
  return STATES[props.state] ?? { tone: 'neutral' as Tone, dot: 'offline' as DotStatus }
})

function elapsed(minutes: number): string {
  if (minutes < 1) return t('device.elapsed.underMinute')
  if (minutes < 60) return t('device.elapsed.minutes', { n: minutes })
  return t('device.elapsed.hours', { h: Math.floor(minutes / 60), m: minutes % 60 })
}

const label = computed(() => {
  if (autoReleased.value) return t('device.status.autoReleased')
  if (!STATES[props.state]) return props.state
  const base = t(`device.status.${props.state}`)
  if (props.state === 'standby' && props.device) {
    const minutes = standbyElapsedMinutes(props.device, now.value)
    if (minutes != null) return t('device.status.standbyFor', { state: base, elapsed: elapsed(minutes) })
  }
  return base
})

const title = computed(() => {
  if (autoReleased.value) return t('device.status.autoReleasedTitle')
  const since = props.state === 'standby' ? props.device?.bluetooth?.standby_since : null
  const at = since ? new Date(since) : null
  return at && !Number.isNaN(at.getTime()) ? t('device.status.standbySince', { time: at.toLocaleString() }) : undefined
})
</script>

<template>
  <SbBadge :tone="look.tone" size="sm" :title="title">
    <SbStatusDot :status="look.dot" size="sm" :label="label" />
    {{ label }}
  </SbBadge>
</template>
