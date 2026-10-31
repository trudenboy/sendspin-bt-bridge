/* The host's hardware: Bluetooth controllers, the devices bonded to them, audio, capabilities. */
import { api, unwrap } from './client'

export function getHardware() {
  return unwrap(api().GET('/api/v1/hardware'))
}

export function getHardwareController(controllerId: string) {
  return unwrap(
    api().GET('/api/v1/hardware/controllers/{controller_id}', { params: { path: { controller_id: controllerId } } }),
  )
}

export function getHardwareBluetoothDevice(mac: string) {
  return unwrap(api().GET('/api/v1/hardware/bluetooth/{mac}', { params: { path: { mac } } }))
}

/* Detail reads: the aggregate carries counts, the drawer needs the full lists. */

export function getHardwareAudioCard(name: string) {
  return unwrap(api().GET('/api/v1/hardware/audio/cards/{name}', { params: { path: { name } } }))
}

export function getHardwareAudioSink(name: string) {
  return unwrap(api().GET('/api/v1/hardware/audio/sinks/{name}', { params: { path: { name } } }))
}

export function getHardwareAudioSource(name: string) {
  return unwrap(api().GET('/api/v1/hardware/audio/sources/{name}', { params: { path: { name } } }))
}
