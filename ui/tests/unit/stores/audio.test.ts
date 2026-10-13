import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('@/api/audio', () => ({ getAudioSinks: vi.fn() }))
vi.mock('@/api/config', () => ({ getConfig: vi.fn(), saveConfig: vi.fn().mockResolvedValue({}) }))

import { getAudioSinks } from '@/api/audio'
import { getConfig, saveConfig } from '@/api/config'
import { useAudioStore } from '@/stores/audio'

const sink = (name: string, in_use_by: string | null = null) => ({
  name,
  description: name,
  state: 'idle',
  sample_spec: 's16le 2ch 44100Hz',
  muted: false,
  volume: 50,
  active_port_available: null,
  identifiers: {},
  in_use_by,
})

describe('useAudioStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('keeps the outputs the bridge lists', async () => {
    vi.mocked(getAudioSinks).mockResolvedValue([sink('a'), sink('b', 'p1')] as never)
    const store = useAudioStore()
    await store.fetchSinks()
    expect(store.sinks.map((s) => s.name)).toEqual(['a', 'b'])
    expect(store.error).toBeNull()
  })

  it('reports why the outputs could not be listed', async () => {
    vi.mocked(getAudioSinks).mockRejectedValue(new Error('nope'))
    const store = useAudioStore()
    await store.fetchSinks()
    expect(store.sinks).toEqual([])
    expect(store.error).toBe('nope')
  })

  it('adds an output as a player without an id, enabled, on that sink', async () => {
    vi.mocked(getConfig).mockResolvedValue({ players: [{ id: 'x', name: 'Old', backend: { type: 'bluetooth_a2dp', mac: 'AA' } }] } as never)
    const store = useAudioStore()
    expect(await store.addAudioOutput('alsa_output.usb', 'Desk', { room: ' Study ' })).toBe(true)
    const saved = vi.mocked(saveConfig).mock.calls[0]![0] as { players: Record<string, unknown>[] }
    expect(saved.players).toHaveLength(2)
    expect(saved.players[1]).toEqual({
      name: 'Desk',
      enabled: true,
      room_name: 'Study',
      backend: { type: 'pulse_sink', sink: 'alsa_output.usb' },
    })
  })

  it('does not add an output another player already uses', async () => {
    vi.mocked(getConfig).mockResolvedValue({ players: [{ id: 'x', name: 'Old', backend: { type: 'pulse_sink', sink: 'alsa_output.usb' } }] } as never)
    const store = useAudioStore()
    expect(await store.addAudioOutput('alsa_output.usb', 'Desk')).toBe(false)
    expect(saveConfig).not.toHaveBeenCalled()
  })
})
