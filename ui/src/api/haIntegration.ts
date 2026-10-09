import { api, unwrap } from './client'

/* These endpoints answer free-form status objects; the fields the UI reads are typed here. */

export interface MqttStatus {
  running?: boolean
  state?: string
  broker?: string | null
  discovery_payload_count?: number
  published_messages?: number
  last_error?: string | null
  last_event_at?: string | null
}

export interface AddonState {
  available?: boolean
  installed?: boolean
  started?: boolean
  install_url?: string
  last_seen?: string | null
  error?: string
}

export interface MdnsStatus {
  advertised: boolean
  service_name?: string
  port?: number
  error?: string
}

export interface MqttProbe {
  found: boolean
  source?: 'supervisor' | 'ma_url' | null
  host?: string
  port?: number
  username?: string
  password_present?: boolean
  ssl?: boolean
  hint?: string
}

export interface ProbeResult {
  ok: boolean
  error?: string
  error_class?: string
  elapsed_ms?: number
}

export async function getMqttStatus() {
  return (await unwrap(api().GET('/api/v1/ha-integration/mqtt'))) as unknown as MqttStatus
}

export async function getMosquitto() {
  return (await unwrap(api().GET('/api/v1/ha-integration/mosquitto'))) as unknown as AddonState
}

export async function getCustomComponent() {
  return (await unwrap(api().GET('/api/v1/ha-integration/custom-component'))) as unknown as AddonState
}

export async function getMdns() {
  return (await unwrap(api().GET('/api/v1/ha-integration/mdns'))) as unknown as MdnsStatus
}

export async function probeMqtt() {
  return (await unwrap(api().POST('/api/v1/ha-integration/mqtt/probe'))) as unknown as MqttProbe
}

export async function testMqtt(body: { host: string; port: number; username: string; password: string; tls: boolean }) {
  return (await unwrap(api().POST('/api/v1/ha-integration/mqtt/test', { body }))) as unknown as ProbeResult
}
