/**
 * Per-speaker settings (an entry of BLUETOOTH_DEVICES), grouped the way the
 * device drawer shows them. Checked against the ``BluetoothDevice`` schema by
 * a unit test, like the bridge layout. Text: ``settings.device.<key>``.
 */
import type { ConfigDoc, FieldDef } from './layout'

const idle = (mode: string) => (d: ConfigDoc) => (d.idle_mode ?? 'default') === mode

export interface DeviceGroup {
  id: 'speaker' | 'idle' | 'sync'
  fields: FieldDef[]
}

export const DEVICE_GROUPS: readonly DeviceGroup[] = [
  {
    id: 'speaker',
    fields: [
      { key: 'player_name', kind: 'text' },
      { key: 'adapter', kind: 'select', optionsFrom: 'adapters', nullable: true },
      { key: 'room_name', kind: 'text' },
      { key: 'room_id', kind: 'text', advanced: true },
      { key: 'volume_controller', kind: 'select', options: ['pa', 'sendspin'], default: 'pa', advanced: true },
      { key: 'listen_port', kind: 'number', min: 1024, max: 65535, nullable: true, placeholder: 'auto', advanced: true },
    ],
  },
  {
    id: 'idle',
    fields: [
      { key: 'idle_mode', kind: 'select', options: ['default', 'power_save', 'auto_disconnect', 'keep_alive'], default: 'default' },
      { key: 'power_save_delay_minutes', kind: 'number', min: 0, max: 60, unit: 'min', default: 1, visible: idle('power_save') },
      { key: 'idle_disconnect_minutes', kind: 'number', min: 0, unit: 'min', default: 0, visible: idle('auto_disconnect') },
      { key: 'keep_alive_method', kind: 'select', options: ['infrasound', 'silence', 'none'], default: 'infrasound', visible: idle('keep_alive') },
      { key: 'keepalive_interval', kind: 'number', min: 30, unit: 's', default: 30, visible: idle('keep_alive') },
    ],
  },
  {
    id: 'sync',
    fields: [
      { key: 'static_delay_ms', kind: 'number', min: 0, max: 5000, step: 10, unit: 'ms' },
      { key: 'min_buffer_ms', kind: 'number', min: 0, max: 30000, step: 50, unit: 'ms', default: 250, advanced: true },
      { key: 'required_lead_time_ms', kind: 'number', min: 0, max: 30000, step: 50, unit: 'ms', default: 250, advanced: true },
    ],
  },
]

/** Keys the drawer edits elsewhere (status switches, calibration) or never edits. */
export const DEVICE_MANAGED_KEYS: readonly string[] = [
  'mac',
  'enabled',
  // Derived by the bridge from idle_mode / keepalive_interval.
  'keepalive_enabled',
  // Written by calibration and delay reports.
  'static_delay_source',
  'static_delay_codec',
  'static_delay_calibrated_at',
]

export const DEVICE_FIELDS: readonly FieldDef[] = DEVICE_GROUPS.flatMap((g) => g.fields)
