import { api, unwrap } from './client'

/** Local audio outputs (PulseAudio/PipeWire sinks) a player can play to. */
export function getAudioSinks() {
  return unwrap(api().GET('/api/v1/audio/sinks'))
}
