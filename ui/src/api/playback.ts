import { apiPost } from './client'

export function setVolume(
  playerName: string,
  volume: number,
  opts?: { forceLocal?: boolean },
) {
  return apiPost<{ success: boolean; volume: number }>('/api/volume', {
    player_name: playerName,
    volume,
    force_local: opts?.forceLocal,
  })
}

export function setMute(playerName: string, muted: boolean) {
  return apiPost<{ success: boolean; muted: boolean }>('/api/mute', {
    player_name: playerName,
    muted,
  })
}

export function pauseDevice(playerName: string) {
  return apiPost<{ success: boolean }>('/api/pause', {
    player_name: playerName,
  })
}

export function pauseAll() {
  return apiPost<{ success: boolean }>('/api/pause_all')
}

export type TransportAction =
  | 'play'
  | 'pause'
  | 'stop'
  | 'next'
  | 'previous'
  | 'repeat_off'
  | 'repeat_one'
  | 'repeat_all'
  | 'shuffle'
  | 'unshuffle'

export function transportCmd(
  action: TransportAction,
  deviceIndex: number,
  value?: number | boolean,
) {
  return apiPost<{ success: boolean }>('/api/transport/cmd', {
    action,
    device_index: deviceIndex,
    value,
  })
}
