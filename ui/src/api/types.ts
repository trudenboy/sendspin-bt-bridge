/* ---------------------------------------------------------------
 * API response types — mirrors Python backend dataclasses.
 * Keep in sync with services/status_snapshot.py, audio_backend.py,
 * player_model.py, event_store.py.
 * --------------------------------------------------------------- */

/* Backend abstraction */

export type BackendType =
  | 'bluetooth_a2dp'
  | 'local_sink'
  | 'usb_audio'
  | 'virtual_sink'
  | 'snapcast_client'
  | 'vban'
  | 'le_audio'

export type PlayerState =
  | 'STREAMING'
  | 'READY'
  | 'CONNECTING'
  | 'ERROR'
  | 'OFFLINE'
  | 'STANDBY'
  | 'IDLE'

export interface BackendInfo {
  type: BackendType
  mac?: string
  capabilities: string[]
}

export interface BackendStatus {
  connected: boolean
  sink_name?: string
  error?: string
}

/* Device / Player snapshot */

export interface DeviceSnapshot {
  player_name: string
  mac: string
  enabled: boolean
  status: string
  connected: boolean
  audio_streaming: boolean
  server_connected: boolean
  volume: number
  muted: boolean
  audio_sink?: string
  adapter?: string
  codec?: string
  sample_rate?: string
  error?: string
  backend_info?: BackendInfo
  player_state?: PlayerState
  events?: DeviceEvent[]
  listen_port?: number
  static_delay_ms?: number
}

export interface DeviceEvent {
  event_type: string
  at: string
  payload?: Record<string, unknown>
}

/* Bridge snapshot */

export interface BridgeSnapshot {
  version: string
  build_date: string
  uptime_seconds: number
  devices: DeviceSnapshot[]
  groups: SyncGroup[]
  adapters: AdapterInfo[]
  ma_connected: boolean
  ma_url?: string
  orchestrator_summary?: OrchestratorSummary
}

export interface OrchestratorSummary {
  players: Record<
    string,
    {
      backend_type: BackendType
      state: PlayerState
      connected: boolean
    }
  >
}

export interface AdapterInfo {
  hci_device: string
  name: string
  mac: string
  powered: boolean
}

export interface SyncGroup {
  group_id: string
  group_name: string
  members: GroupMember[]
}

export interface GroupMember {
  player_id: string
  player_name: string
  state: string
}

/* MA types */

export interface NowPlaying {
  title?: string
  artist?: string
  album?: string
  artwork_url?: string
  duration?: number
  position?: number
  state: string
}

/* Events */

export interface EventRecord {
  event_type: string
  subject_id: string
  category: string
  payload: Record<string, unknown>
  at: string
}

export interface EventStoreStats {
  total_events: number
  buffer_capacity: number
  unique_subjects: number
  event_types: string[]
}

/* Config */

export interface BridgeConfig {
  BRIDGE_NAME: string
  SENDSPIN_SERVER: string
  SENDSPIN_PORT: number
  WEB_PORT: number
  TZ: string
  LOG_LEVEL: string
  BLUETOOTH_DEVICES: BluetoothDeviceConfig[]
  players: PlayerConfig[]
  adapters: AdapterConfig[]
  MA_API_URL: string
  MA_API_TOKEN: string
  VOLUME_VIA_MA: boolean
  PULSE_LATENCY_MSEC: number
  PREFER_SBC_CODEC: boolean
  BT_CHECK_INTERVAL: number
  BT_MAX_RECONNECT_FAILS: number
  [key: string]: unknown
}

export interface BluetoothDeviceConfig {
  mac: string
  player_name: string
  adapter?: string
  enabled: boolean
  listen_port?: number
  static_delay_ms?: number
}

export interface PlayerConfig {
  id: string
  player_name: string
  backend: {
    type: BackendType
    mac?: string
    adapter?: string
    sink_name?: string
  }
  enabled: boolean
  listen_port?: number
  static_delay_ms?: number
}

export interface AdapterConfig {
  hci_device: string
  name: string
}

/* Async jobs */

export interface AsyncJob<T = unknown> {
  job_id: string
  status: 'running' | 'completed' | 'failed'
  result?: T
  error?: string
}

/* BT scan */

export interface BtScanDevice {
  mac: string
  name: string
  rssi?: number
  is_audio: boolean
  paired: boolean
}
