/**
 * Backend Descriptor Registry
 *
 * Central registry that maps BackendType → UI descriptor.
 * Adding a new backend type to the UI = adding an entry here.
 * Components use this to render backend-specific icons, labels,
 * config fields, status fields, and signal path segments.
 */

import { type Component, markRaw } from 'vue'
import {
  Bluetooth,
  Speaker,
  Usb,
  Radio,
  Podcast,
  AudioLines,
  Headphones,
} from 'lucide-vue-next'

export type BackendType =
  | 'bluetooth_a2dp'
  | 'local_sink'
  | 'usb_audio'
  | 'virtual_sink'
  | 'snapcast_client'
  | 'vban'
  | 'le_audio'

export interface BackendConfigField {
  key: string
  labelKey: string
  type: 'text' | 'number' | 'toggle' | 'select' | 'slider'
  options?: { value: string; labelKey: string }[]
  min?: number
  max?: number
  step?: number
  required?: boolean
}

export interface BackendStatusField {
  key: string
  labelKey: string
  format?: 'text' | 'badge' | 'code'
}

export interface SignalPathSegment {
  id: string
  labelKey: string
}

export interface BackendDescriptor {
  type: BackendType
  labelKey: string
  icon: Component
  color: string
  configFields: BackendConfigField[]
  statusFields: BackendStatusField[]
  signalPath: SignalPathSegment[]
}

export const BACKEND_REGISTRY: Record<BackendType, BackendDescriptor> = {
  bluetooth_a2dp: {
    type: 'bluetooth_a2dp',
    labelKey: 'device.backend.bluetooth_a2dp',
    icon: markRaw(Bluetooth),
    color: 'primary',
    configFields: [
      { key: 'mac', labelKey: 'drawer.config.mac', type: 'text', required: true },
      { key: 'adapter', labelKey: 'drawer.config.adapter', type: 'select' },
      { key: 'port', labelKey: 'drawer.config.port', type: 'number', min: 1024, max: 65535 },
      { key: 'delay', labelKey: 'drawer.config.delay', type: 'slider', min: -1000, max: 0, step: 50 },
    ],
    statusFields: [
      { key: 'player_state', labelKey: 'drawer.status.playerState', format: 'badge' },
      { key: 'audio_sink', labelKey: 'drawer.status.audioSink', format: 'code' },
      { key: 'codec', labelKey: 'drawer.status.codec', format: 'text' },
      { key: 'sample_rate', labelKey: 'drawer.status.sampleRate', format: 'text' },
    ],
    signalPath: [
      { id: 'ma', labelKey: 'app.ma' },
      { id: 'sendspin', labelKey: 'app.title' },
      { id: 'subprocess', labelKey: 'drawer.signal.subprocess' },
      { id: 'pulse_sink', labelKey: 'drawer.status.audioSink' },
      { id: 'speaker', labelKey: 'device.backend.bluetooth_a2dp' },
    ],
  },

  local_sink: {
    type: 'local_sink',
    labelKey: 'device.backend.local_sink',
    icon: markRaw(Speaker),
    color: 'success',
    configFields: [
      { key: 'sink_name', labelKey: 'drawer.status.audioSink', type: 'select', required: true },
      { key: 'port', labelKey: 'drawer.config.port', type: 'number', min: 1024, max: 65535 },
    ],
    statusFields: [
      { key: 'player_state', labelKey: 'drawer.status.playerState', format: 'badge' },
      { key: 'audio_sink', labelKey: 'drawer.status.audioSink', format: 'code' },
    ],
    signalPath: [
      { id: 'ma', labelKey: 'app.ma' },
      { id: 'sendspin', labelKey: 'app.title' },
      { id: 'subprocess', labelKey: 'drawer.signal.subprocess' },
      { id: 'local_sink', labelKey: 'device.backend.local_sink' },
    ],
  },

  usb_audio: {
    type: 'usb_audio',
    labelKey: 'device.backend.usb_audio',
    icon: markRaw(Usb),
    color: 'accent',
    configFields: [
      { key: 'device_path', labelKey: 'drawer.status.audioSink', type: 'select', required: true },
      { key: 'port', labelKey: 'drawer.config.port', type: 'number', min: 1024, max: 65535 },
    ],
    statusFields: [
      { key: 'player_state', labelKey: 'drawer.status.playerState', format: 'badge' },
      { key: 'device_path', labelKey: 'drawer.status.audioSink', format: 'code' },
    ],
    signalPath: [
      { id: 'ma', labelKey: 'app.ma' },
      { id: 'sendspin', labelKey: 'app.title' },
      { id: 'subprocess', labelKey: 'drawer.signal.subprocess' },
      { id: 'usb_device', labelKey: 'device.backend.usb_audio' },
    ],
  },

  virtual_sink: {
    type: 'virtual_sink',
    labelKey: 'device.backend.virtual_sink',
    icon: markRaw(AudioLines),
    color: 'info',
    configFields: [
      { key: 'sink_name', labelKey: 'drawer.status.audioSink', type: 'text', required: true },
      { key: 'port', labelKey: 'drawer.config.port', type: 'number', min: 1024, max: 65535 },
    ],
    statusFields: [
      { key: 'player_state', labelKey: 'drawer.status.playerState', format: 'badge' },
      { key: 'audio_sink', labelKey: 'drawer.status.audioSink', format: 'code' },
    ],
    signalPath: [
      { id: 'ma', labelKey: 'app.ma' },
      { id: 'sendspin', labelKey: 'app.title' },
      { id: 'subprocess', labelKey: 'drawer.signal.subprocess' },
      { id: 'virtual_sink', labelKey: 'device.backend.virtual_sink' },
    ],
  },

  snapcast_client: {
    type: 'snapcast_client',
    labelKey: 'device.backend.snapcast_client',
    icon: markRaw(Radio),
    color: 'warning',
    configFields: [
      { key: 'snapserver', labelKey: 'drawer.status.audioSink', type: 'text', required: true },
      { key: 'client_id', labelKey: 'drawer.config.name', type: 'text' },
      { key: 'port', labelKey: 'drawer.config.port', type: 'number', min: 1024, max: 65535 },
    ],
    statusFields: [
      { key: 'player_state', labelKey: 'drawer.status.playerState', format: 'badge' },
      { key: 'snapserver', labelKey: 'drawer.status.audioSink', format: 'code' },
    ],
    signalPath: [
      { id: 'ma', labelKey: 'app.ma' },
      { id: 'sendspin', labelKey: 'app.title' },
      { id: 'snapserver', labelKey: 'device.backend.snapcast_client' },
      { id: 'client', labelKey: 'drawer.config.name' },
    ],
  },

  vban: {
    type: 'vban',
    labelKey: 'device.backend.vban',
    icon: markRaw(Podcast),
    color: 'error',
    configFields: [
      { key: 'target_ip', labelKey: 'drawer.status.audioSink', type: 'text', required: true },
      { key: 'stream_name', labelKey: 'drawer.config.name', type: 'text' },
      { key: 'port', labelKey: 'drawer.config.port', type: 'number', min: 1024, max: 65535 },
    ],
    statusFields: [
      { key: 'player_state', labelKey: 'drawer.status.playerState', format: 'badge' },
      { key: 'target_ip', labelKey: 'drawer.status.audioSink', format: 'code' },
    ],
    signalPath: [
      { id: 'ma', labelKey: 'app.ma' },
      { id: 'sendspin', labelKey: 'app.title' },
      { id: 'vban_stream', labelKey: 'device.backend.vban' },
      { id: 'receiver', labelKey: 'drawer.config.name' },
    ],
  },

  le_audio: {
    type: 'le_audio',
    labelKey: 'device.backend.le_audio',
    icon: markRaw(Headphones),
    color: 'primary',
    configFields: [
      { key: 'mac', labelKey: 'drawer.config.mac', type: 'text', required: true },
      { key: 'adapter', labelKey: 'drawer.config.adapter', type: 'select' },
      { key: 'port', labelKey: 'drawer.config.port', type: 'number', min: 1024, max: 65535 },
    ],
    statusFields: [
      { key: 'player_state', labelKey: 'drawer.status.playerState', format: 'badge' },
      { key: 'audio_sink', labelKey: 'drawer.status.audioSink', format: 'code' },
    ],
    signalPath: [
      { id: 'ma', labelKey: 'app.ma' },
      { id: 'sendspin', labelKey: 'app.title' },
      { id: 'subprocess', labelKey: 'drawer.signal.subprocess' },
      { id: 'le_sink', labelKey: 'device.backend.le_audio' },
    ],
  },
}

export function getBackendDescriptor(type: string): BackendDescriptor {
  return BACKEND_REGISTRY[type as BackendType] ?? BACKEND_REGISTRY.bluetooth_a2dp
}

export function getBackendIcon(type: string): Component {
  return getBackendDescriptor(type).icon
}

export function getBackendColor(type: string): string {
  return getBackendDescriptor(type).color
}
