import { ref, computed, watch, onUnmounted, type Ref } from 'vue'
import type { DeviceSnapshot, MaNowPlaying } from '@/api/types'
import { useMaStore } from '@/stores/ma'

/** Format seconds into M:SS or H:MM:SS. */
export function formatTime(totalSeconds: number): string {
  const s = Math.max(0, Math.round(totalSeconds))
  const hours = Math.floor(s / 3600)
  const minutes = Math.floor((s % 3600) / 60)
  const seconds = s % 60
  const mm = hours > 0 ? String(minutes).padStart(2, '0') : String(minutes)
  const ss = String(seconds).padStart(2, '0')
  return hours > 0 ? `${hours}:${mm}:${ss}` : `${mm}:${ss}`
}

interface ProgressSource {
  elapsed: number
  duration: number
  updatedAt: number
  playing: boolean
}

function resolveMaSource(np: MaNowPlaying): ProgressSource | null {
  const duration = np.duration ?? 0
  if (duration <= 0) return null
  const elapsed = np.elapsed ?? 0
  const updatedAt = np.elapsed_updated_at ?? Date.now() / 1000
  const playing = np.state === 'playing'
  return { elapsed, duration, updatedAt, playing }
}

function resolveNativeSource(device: DeviceSnapshot): ProgressSource | null {
  const duration = device.track_duration_ms
  if (!duration || duration <= 0) return null
  const elapsed = (device.track_progress_ms ?? 0) / 1000
  const dur = duration / 1000
  const playing =
    device.audio_streaming || device.player_state === 'STREAMING'
  return { elapsed, duration: dur, updatedAt: Date.now() / 1000, playing }
}

export function usePlaybackProgress(deviceRef: Ref<DeviceSnapshot>) {
  const maStore = useMaStore()

  const elapsed = ref(0)
  const duration = ref(0)
  const hasProgress = ref(false)

  let timerHandle: ReturnType<typeof setInterval> | null = null
  let lastSource: ProgressSource | null = null

  function resolveSource(): ProgressSource | null {
    const device = deviceRef.value
    // Priority 1: MA now-playing embedded in device snapshot
    if (device.ma_now_playing) {
      const src = resolveMaSource(device.ma_now_playing)
      if (src) return src
    }
    // Priority 2: MA store now-playing (global, any playing group)
    for (const np of Object.values(maStore.nowPlaying) as MaNowPlaying[]) {
      const src = resolveMaSource(np)
      if (src) return src
    }
    // Priority 3: Native track progress from subprocess
    return resolveNativeSource(device)
  }

  function tick() {
    const src = resolveSource()
    if (!src) {
      hasProgress.value = false
      elapsed.value = 0
      duration.value = 0
      lastSource = null
      return
    }

    lastSource = src
    duration.value = src.duration

    if (src.playing) {
      const now = Date.now() / 1000
      const delta = now - src.updatedAt
      elapsed.value = Math.min(src.elapsed + delta, src.duration)
    } else {
      elapsed.value = src.elapsed
    }

    hasProgress.value = true
  }

  function startTimer() {
    stopTimer()
    tick()
    timerHandle = setInterval(tick, 1000)
  }

  function stopTimer() {
    if (timerHandle !== null) {
      clearInterval(timerHandle)
      timerHandle = null
    }
  }

  // React to device/store changes
  watch(
    () => [
      deviceRef.value.ma_now_playing,
      deviceRef.value.track_progress_ms,
      deviceRef.value.track_duration_ms,
      deviceRef.value.audio_streaming,
      deviceRef.value.player_state,
      maStore.nowPlaying,
    ],
    () => tick(),
    { deep: true },
  )

  startTimer()
  onUnmounted(stopTimer)

  const progressPct = computed(() =>
    duration.value > 0
      ? Math.min(100, (elapsed.value / duration.value) * 100)
      : 0,
  )

  const elapsedText = computed(() => formatTime(elapsed.value))
  const durationText = computed(() => formatTime(duration.value))

  return {
    progressPct,
    elapsed,
    duration,
    elapsedText,
    durationText,
    hasProgress,
  }
}
