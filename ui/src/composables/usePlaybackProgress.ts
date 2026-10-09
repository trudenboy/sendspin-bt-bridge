import { ref, computed, watch, onUnmounted, type Ref } from 'vue'
import type { Device } from '@/api/types'

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

/** Music Assistant's now-playing as the bridge caches it (seconds, epoch seconds). */
export interface MaNowPlaying {
  state?: string
  track?: string
  artist?: string
  album?: string
  image_url?: string
  elapsed?: number
  elapsed_updated_at?: number
  duration?: number
  [key: string]: unknown
}

function resolveMaSource(np: MaNowPlaying | null | undefined): ProgressSource | null {
  const duration = np?.duration ?? 0
  if (!np || duration <= 0) return null
  return {
    elapsed: np.elapsed ?? 0,
    duration,
    updatedAt: np.elapsed_updated_at ?? Date.now() / 1000,
    playing: np.state === 'playing',
  }
}

function resolveNativeSource(device: Device): ProgressSource | null {
  const { duration_ms: duration, progress_ms: progress } = device.playback.track
  if (!duration || duration <= 0) return null
  return {
    elapsed: (progress ?? 0) / 1000,
    duration: duration / 1000,
    updatedAt: Date.now() / 1000,
    playing: device.playback.playing && device.audio.streaming,
  }
}

export function usePlaybackProgress(deviceRef: Ref<Device>) {
  const elapsed = ref(0)
  const duration = ref(0)
  const hasProgress = ref(false)

  let timerHandle: ReturnType<typeof setInterval> | null = null

  function resolveSource(): ProgressSource | null {
    const device = deviceRef.value
    // Music Assistant's clock first (it knows seeks); the daemon's track position otherwise.
    return resolveMaSource(device.music_assistant.now_playing as MaNowPlaying | null) ?? resolveNativeSource(device)
  }

  function tick() {
    const src = resolveSource()
    if (!src) {
      hasProgress.value = false
      elapsed.value = 0
      duration.value = 0
      return
    }

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
      deviceRef.value.music_assistant.now_playing,
      deviceRef.value.playback.track,
      deviceRef.value.playback.playing,
      deviceRef.value.audio.streaming,
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
