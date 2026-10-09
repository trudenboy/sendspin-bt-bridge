import type { BridgeStatus, Device } from '@/api/types'
import { makeDevice } from './device'

/** A full ``GET /status`` document around the given devices. */
export function makeStatus(devices: Device[] = [makeDevice()]): BridgeStatus {
  return {
    bridge: {
      version: '3.0.0-beta.15',
      build_date: '2026-10-09',
      hostname: 'bridge',
      ip_address: '192.168.1.2',
      uptime: '1:00:00',
      runtime: 'docker',
      runtime_mode: 'production',
      config_schema_version: 5,
      ipc_protocol_version: 2,
      auth_enabled: false,
      ma_connected: false,
      ma_web_url: null,
      device_count: devices.length,
      disabled_devices: [],
      startup: null,
      update_available: null,
      mock_runtime: null,
      preflight: null,
      state_model: null,
      guidance: null,
      onboarding: null,
      recovery: null,
    },
    devices,
    groups: [],
  } as unknown as BridgeStatus
}
