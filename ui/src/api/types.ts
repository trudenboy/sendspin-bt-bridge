/* Types of the API v1 contract, generated from openapi.json (npm run gen:api). */
import type { Schemas } from './client'

export type Bridge = Schemas['Bridge']
export type BridgeStatus = Schemas['BridgeStatus']
export type Device = Schemas['Device']
export type DisabledDevice = Schemas['DisabledDevice']
export type Group = Schemas['Group']
export type GroupMember = Schemas['GroupMember']
export type Job = Schemas['Job']
export type JobError = Schemas['JobError']
export type Adapter = Schemas['AdapterOut']
export type KnownDevice = Schemas['KnownDevice']
export type BridgeConfig = Schemas['BridgeConfig']
export type Validation = Schemas['Validation']
export type SaveResult = Schemas['SaveResult']
export type SessionState = Schemas['SessionState']
export type SignInResult = Schemas['SignInResult']
export type TokenRecord = Schemas['TokenRecord']
export type Track = Schemas['Track']
export type StartupProgress = Schemas['StartupProgress']
export type QueueCommandOut = Schemas['QueueCommandOut']
export type BugReportOut = Schemas['BugReportOut']

export type JobStatus = Job['status']
export type AuthMethod = Schemas['SignInRequest']['method']
export type TransportCommand = Schemas['TransportIn']['command']
export type QueueAction = Schemas['QueueCommandIn']['action']

/** A device found by a Bluetooth scan (the scan job's result). */
export interface ScanDevice {
  mac: string
  name: string
  adapter: string
  rssi_dbm?: number | null
  audio_capable?: boolean
  supports_import?: boolean
  kind?: string
  paired?: boolean
  [key: string]: unknown
}

export interface ScanResult {
  devices: ScanDevice[]
  stats: Record<string, unknown>
}

/** One envelope for every event on /api/v1/events. */
export interface EventEnvelope<T = unknown> {
  v: number
  type: 'status' | 'job' | 'log'
  at: string
  data: T
}

/** ``bluetoothctl info`` fields for one device ("yes"/"no" strings) plus the raw lines. */
export interface BtDeviceInfo {
  mac: string
  name?: string
  alias?: string
  paired?: string
  bonded?: string
  trusted?: string
  blocked?: string
  connected?: string
  class?: string
  icon?: string
  raw?: string[]
  [key: string]: unknown
}

/** A Music Assistant sync group as the bridge mirrors it. */
export interface MaGroup {
  id: string
  name: string
  members: { id: string; name?: string; state?: string; [key: string]: unknown }[]
  [key: string]: unknown
}

/** Music Assistant's now-playing (``image_url`` is a signed same-origin proxy path). */
export interface NowPlaying {
  connected?: boolean
  state?: string
  track?: string
  artist?: string
  album?: string
  image_url?: string
  elapsed?: number
  elapsed_updated_at?: number
  duration?: number
  shuffle?: boolean
  repeat?: string
  syncgroup_id?: string
  [key: string]: unknown
}
