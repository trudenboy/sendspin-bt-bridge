/* Local audio outputs: the inventory and the safe commands on them. */
import { api, unwrap } from './client'

/** Local audio outputs (PulseAudio/PipeWire sinks) a player can play to. */
export function getAudioSinks() {
  return unwrap(api().GET('/api/v1/audio/sinks'))
}

const path = (name: string) => ({ params: { path: { name } } })

/** Set one sink's level, 0-100; the response is the sink's state after the write. */
export function setSinkVolume(name: string, volume: number) {
  return unwrap(api().PUT('/api/v1/audio/sinks/{name}/volume', { ...path(name), body: { volume } }))
}

/** Mute (``true``) or unmute (``false``) one sink. */
export function setSinkMute(name: string, muted: boolean) {
  return unwrap(api().PUT('/api/v1/audio/sinks/{name}/mute', { ...path(name), body: { muted } }))
}

/** Make one sink the audio server's default. */
export function setDefaultSink(name: string) {
  return unwrap(api().PUT('/api/v1/audio/default-sink', { body: { name } }))
}

/** Suspend a sink: it keeps existing, but no longer plays. */
export function suspendSink(name: string) {
  return unwrap(api().POST('/api/v1/audio/sinks/{name}/suspend', path(name)))
}

/** Resume a suspended sink. */
export function resumeSink(name: string) {
  return unwrap(api().POST('/api/v1/audio/sinks/{name}/resume', path(name)))
}
