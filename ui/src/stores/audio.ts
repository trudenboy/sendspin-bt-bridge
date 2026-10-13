import { defineStore } from 'pinia'
import { ref } from 'vue'
import { getAudioSinks } from '@/api/audio'
import { getConfig, saveConfig } from '@/api/config'
import type { AudioSink } from '@/api/types'

export const useAudioStore = defineStore('audio', () => {
  const sinks = ref<AudioSink[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function fetchSinks() {
    loading.value = true
    error.value = null
    try {
      sinks.value = await getAudioSinks()
    } catch (e) {
      sinks.value = []
      error.value = e instanceof Error ? e.message : String(e)
    } finally {
      loading.value = false
    }
  }

  /**
   * Make a local audio output a player: append it to the configuration (the
   * whole document is saved; the bridge assigns the id and starts the player).
   * Resolves ``false`` when another player already uses that output.
   */
  async function addAudioOutput(sink: string, name: string, extra: { room?: string } = {}) {
    const config = (await getConfig()) as Record<string, unknown>
    const players = (config.players as Record<string, unknown>[] | undefined) ?? []
    const taken = players.some((p) => {
      const backend = p.backend as { type?: string; sink?: string } | undefined
      return backend?.type === 'pulse_sink' && backend.sink === sink
    })
    if (taken) return false
    const player: Record<string, unknown> = {
      name: name || sink,
      enabled: true,
      backend: { type: 'pulse_sink', sink },
    }
    if (extra.room?.trim()) player.room_name = extra.room.trim()
    await saveConfig({ ...config, players: [...players, player] } as never)
    return true
  }

  return { sinks, loading, error, fetchSinks, addAudioOutput }
})
