/* The host's hardware: Bluetooth controllers, the devices bonded to them, capabilities. */
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
