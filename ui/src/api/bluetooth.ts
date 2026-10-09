/* Controllers, discovery, pairing and the BlueZ device cache. */
import { api, unwrap } from './client'

export function getAdapters() {
  return unwrap(api().GET('/api/v1/adapters'))
}

export function setAdapterPower(adapterId: string, on: boolean) {
  return unwrap(
    api().PUT('/api/v1/adapters/{adapter_id}/power', { params: { path: { adapter_id: adapterId } }, body: { on } }),
  )
}

export function getKnownDevices(namedOnly = true) {
  return unwrap(api().GET('/api/v1/bluetooth/devices', { params: { query: { named_only: namedOnly } } }))
}

export function getBtDeviceInfo(mac: string, adapter = '') {
  return unwrap(
    api().GET('/api/v1/bluetooth/devices/{mac}', { params: { path: { mac }, query: { adapter } } }),
  )
}

export function disconnectBtDevice(mac: string) {
  return unwrap(api().POST('/api/v1/bluetooth/devices/{mac}/disconnect', { params: { path: { mac } } }))
}

export function forgetBtDevice(mac: string, adapterMac = '') {
  return unwrap(
    api().DELETE('/api/v1/bluetooth/devices/{mac}', { params: { path: { mac }, query: { adapter_mac: adapterMac } } }),
  )
}

export function startScan(adapter: string, audioOnly = true) {
  return unwrap(api().POST('/api/v1/bluetooth/scans', { body: { adapter, audio_only: audioOnly } }))
}

export interface PairOptions {
  adapter?: string
  quiesceAdapter?: boolean
  noInputNoOutputAgent?: boolean
  allowHfpProfile?: boolean
}

export function startPairing(mac: string, opts: PairOptions = {}) {
  return unwrap(
    api().POST('/api/v1/bluetooth/pairings', {
      body: {
        mac,
        adapter: opts.adapter ?? '',
        quiesce_adapter: opts.quiesceAdapter ?? false,
        no_input_no_output_agent: opts.noInputNoOutputAgent ?? false,
        allow_hfp_profile: opts.allowHfpProfile ?? false,
      },
    }),
  )
}

export function startReset(mac: string, opts: Omit<PairOptions, 'quiesceAdapter'> = {}) {
  return unwrap(
    api().POST('/api/v1/bluetooth/resets', {
      body: {
        mac,
        adapter: opts.adapter ?? '',
        no_input_no_output_agent: opts.noInputNoOutputAgent ?? false,
        allow_hfp_profile: opts.allowHfpProfile ?? false,
      },
    }),
  )
}
