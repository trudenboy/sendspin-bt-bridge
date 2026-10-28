/* Commands on one bridge speaker, addressed by its stable device id. */
import { api, unwrap } from './client'

const path = (deviceId: string) => ({ params: { path: { device_id: deviceId } } })

export function setDeviceEnabled(deviceId: string, enabled: boolean) {
  return unwrap(api().PATCH('/api/v1/devices/{device_id}', { ...path(deviceId), body: { enabled } }))
}

/** Opt the speaker out of (false) or back into (true) playout sync; its player restarts to apply it. */
export function setDevicePlayoutSync(deviceId: string, playoutSyncEnabled: boolean) {
  return unwrap(api().PATCH('/api/v1/devices/{device_id}', { ...path(deviceId), body: { playout_sync_enabled: playoutSyncEnabled } }))
}

/** Take the speaker off the bridge; the bridge also removes its Bluetooth pairing. */
export function removeDevice(deviceId: string) {
  return unwrap(api().DELETE('/api/v1/devices/{device_id}', path(deviceId)))
}

/** Forget the speaker's air calibration for this codec, SBC rate and adapter; applied live. */
export function forgetAirCalibration(deviceId: string, codec: string, rate: number, adapter: string) {
  return unwrap(
    api().DELETE('/api/v1/devices/{device_id}/air-calibrations/{codec}/{rate}/{adapter}', {
      params: { path: { device_id: deviceId, codec, rate, adapter } },
    }),
  )
}

export function setManagement(deviceId: string, enabled: boolean) {
  return unwrap(api().PUT('/api/v1/devices/{device_id}/management', { ...path(deviceId), body: { enabled } }))
}

export function setPowerSave(deviceId: string, enabled: boolean) {
  return unwrap(api().PUT('/api/v1/devices/{device_id}/power-save', { ...path(deviceId), body: { enabled } }))
}

export function standbyDevice(deviceId: string) {
  return unwrap(api().POST('/api/v1/devices/{device_id}/standby', path(deviceId)))
}

export function wakeDevice(deviceId: string) {
  return unwrap(api().POST('/api/v1/devices/{device_id}/wake', path(deviceId)))
}

export function reconnectDevice(deviceId: string) {
  return unwrap(api().POST('/api/v1/devices/{device_id}/reconnect', path(deviceId)))
}

export function repairDevice(deviceId: string, quiesceAdapter = false) {
  return unwrap(
    api().POST('/api/v1/devices/{device_id}/repair', { ...path(deviceId), body: { quiesce_adapter: quiesceAdapter } }),
  )
}

export function claimAudio(deviceId: string) {
  return unwrap(api().POST('/api/v1/devices/{device_id}/claim', path(deviceId)))
}

export function openPairingWindow(deviceId: string) {
  return unwrap(api().POST('/api/v1/devices/{device_id}/pairing-window', path(deviceId)))
}

export function unmuteSink(deviceId: string) {
  return unwrap(api().POST('/api/v1/devices/{device_id}/unmute-sink', path(deviceId)))
}

export function setLatency(
  deviceId: string,
  value: number,
  opts: { field?: 'static_delay_ms' | 'required_lead_time_ms' | 'min_buffer_ms'; source?: string; revision?: string } = {},
) {
  return unwrap(
    api().PUT('/api/v1/devices/{device_id}/latency', {
      ...path(deviceId),
      body: {
        field: opts.field ?? 'static_delay_ms',
        value,
        source: opts.source ?? 'manual',
        recommendation_revision: opts.revision ?? null,
      },
    }),
  )
}
