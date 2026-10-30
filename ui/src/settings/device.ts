/**
 * Per-speaker settings (an entry of ``players``), grouped the way the
 * device drawer shows them. Checked against the ``Player`` schema by
 * a unit test, like the bridge layout. Text: ``settings.device.<key>``.
 */
import { getPath, type ConfigDoc, type FieldDef } from './layout'

const idle = (mode: string) => (d: ConfigDoc) => (d.idle_mode ?? 'default') === mode

export type PlayerBackend = 'bluetooth_a2dp' | 'pulse_sink'

/** A player field; ``backend`` limits it to one kind of player (all by default). */
export interface DeviceField extends FieldDef {
  backend?: PlayerBackend
}

export interface DeviceGroup {
  id: 'speaker' | 'idle' | 'sync'
  fields: DeviceField[]
}

/** The kind of a config entry; entries without a backend are Bluetooth speakers. */
export function playerBackend(doc: ConfigDoc): PlayerBackend {
  return getPath(doc, 'backend.type') === 'pulse_sink' ? 'pulse_sink' : 'bluetooth_a2dp'
}

/** Whether the field applies to this player's backend and its own visibility rule. */
export function deviceFieldVisible(f: DeviceField, doc: ConfigDoc): boolean {
  return (!f.backend || f.backend === playerBackend(doc)) && (!f.visible || f.visible(doc))
}

// A local audio output has no link to drop and cannot play the infrasound burst
// (the bridge never wakes a shared output with sound it cannot hear).
const NOT_FOR_OUTPUT: Record<string, readonly string[]> = {
  idle_mode: ['auto_disconnect'],
  keep_alive_method: ['infrasound'],
}
const OUTPUT_DEFAULTS: Record<string, unknown> = { volume_controller: 'sendspin', keep_alive_method: 'silence' }

/** The field as this player shows it: options and displayed default per backend. */
export function deviceFieldFor(f: DeviceField, doc: ConfigDoc): DeviceField {
  if (playerBackend(doc) !== 'pulse_sink') return f
  const drop = NOT_FOR_OUTPUT[f.key]
  const out: DeviceField = { ...f }
  if (drop && f.options) out.options = f.options.filter((o) => !drop.includes(o))
  if (f.key in OUTPUT_DEFAULTS) out.default = OUTPUT_DEFAULTS[f.key]
  return out
}

export const DEVICE_GROUPS: readonly DeviceGroup[] = [
  {
    id: 'speaker',
    fields: [
      { key: 'name', kind: 'text', default: '' },
      { key: 'backend.adapter', kind: 'select', optionsFrom: 'adapters', nullable: true, backend: 'bluetooth_a2dp' },
      { key: 'backend.sink', kind: 'select', optionsFrom: 'sinks', backend: 'pulse_sink' },
      { key: 'room_name', kind: 'text' },
      { key: 'room_id', kind: 'text', advanced: true },
      { key: 'volume_controller', kind: 'select', options: ['pa', 'sendspin'], default: 'pa', advanced: true },
      // Off by default: playback is A2DP-only and the bridge switches a saved
      // headset profile back (calls are not part of the bridge's job).
      { key: 'allow_hfp_profile', kind: 'toggle', default: false, backend: 'bluetooth_a2dp' },
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
  // Identity and backend (type / MAC) are fixed once a player exists.
  'id',
  'backend',
  'enabled',
  // Applied at once from its own switch (PATCH), not saved with the form.
  'playout_sync_enabled',
  // Not edited in the app: the bind address and the format hint.
  'listen_host',
  'preferred_format',
  // Derived by the bridge from idle_mode / keepalive_interval.
  'keepalive_enabled',
  // Written by calibration and delay reports.
  'static_delay_source',
  'static_delay_codec',
  'static_delay_calibrated_at',
  // Written by the sync wizard or learned by the bridge; forgotten from the Timing panel.
  'air_to_sound_latency',
  'air_learned_latency',
]

export const DEVICE_FIELDS: readonly DeviceField[] = DEVICE_GROUPS.flatMap((g) => g.fields)
