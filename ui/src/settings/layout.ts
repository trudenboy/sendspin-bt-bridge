/**
 * What the settings screen shows, section by section.
 *
 * Every key of the bridge config is either a field here, owned by one of the
 * section components (adapters, Music Assistant sign-in, tokens, ...), or
 * managed by the bridge itself and never edited by hand. A unit test checks
 * this file against the API's ``BridgeConfig`` schema, so a new config key
 * cannot be forgotten and limits/options stay in step with the server.
 *
 * Sections are named after what their settings do (Music Assistant's rule),
 * and labels/help come from i18n: ``settings.fields.<key>.label|help``.
 */

export type FieldKind = 'toggle' | 'number' | 'text' | 'password' | 'select' | 'list'
export type Unit = 'ms' | 's' | 'min' | 'h'

// The config document as the settings screen edits it (a loose JSON object).
export type ConfigDoc = Record<string, unknown>

export interface FieldDef {
  /** Config path; nested objects use dots (``HA_INTEGRATION.mqtt.port``). */
  key: string
  kind: FieldKind
  options?: readonly string[]
  /** Choices supplied at runtime (the bridge's adapters) instead of a schema enum. */
  optionsFrom?: 'adapters'
  min?: number
  max?: number
  step?: number
  unit?: Unit
  /** Shown when the document has no value; must equal the schema default. */
  default?: unknown
  /** An empty input stores ``null`` (the bridge then picks the value). */
  nullable?: boolean
  placeholder?: string
  /** Hidden until "Show advanced settings" is on. */
  advanced?: boolean
  visible?: (config: ConfigDoc) => boolean
  /** Extra writes that keep related keys consistent with this one. */
  onChange?: (value: unknown, set: (key: string, value: unknown) => void) => void
}

export type SectionComponent =
  | 'maConnection'
  | 'sendspinTest'
  | 'latencyAssistant'
  | 'adapters'
  | 'haStatus'
  | 'areaMap'
  | 'password'
  | 'tokens'
  | 'updates'
  | 'backup'

export interface SectionDef {
  id: string
  icon: string
  /** Rendered above the fields. */
  before?: SectionComponent[]
  fields: FieldDef[]
  /** Rendered below the fields. */
  after?: SectionComponent[]
}

export function getPath(config: ConfigDoc | null | undefined, key: string): unknown {
  let cur: unknown = config
  for (const part of key.split('.')) {
    if (cur == null || typeof cur !== 'object') return undefined
    cur = (cur as Record<string, unknown>)[part]
  }
  return cur
}

const haMode = (c: ConfigDoc) => getPath(c, 'HA_INTEGRATION.mode')
const bruteForceOn = (c: ConfigDoc) => getPath(c, 'BRUTE_FORCE_PROTECTION') !== false

export const SECTIONS: readonly SectionDef[] = [
  {
    id: 'general',
    icon: 'settings',
    fields: [
      { key: 'BRIDGE_NAME', kind: 'text', placeholder: 'auto' },
      { key: 'TZ', kind: 'text', placeholder: 'Europe/Berlin' },
      { key: 'WEB_PORT', kind: 'number', min: 1, max: 65535, nullable: true, placeholder: '8080', advanced: true },
      { key: 'LOG_LEVEL', kind: 'select', options: ['DEBUG', 'INFO', 'WARNING', 'ERROR'] },
    ],
  },
  {
    id: 'musicAssistant',
    icon: 'music',
    before: ['maConnection'],
    fields: [
      { key: 'SENDSPIN_SERVER', kind: 'text', placeholder: 'auto' },
      { key: 'SENDSPIN_PORT', kind: 'number', min: 1, max: 65535 },
      { key: 'BASE_LISTEN_PORT', kind: 'number', min: 1024, max: 65535, nullable: true, placeholder: 'auto', advanced: true },
      { key: 'SENDSPIN_PAIRING', kind: 'toggle' },
      { key: 'VOLUME_VIA_MA', kind: 'toggle' },
      { key: 'MUTE_VIA_MA', kind: 'toggle' },
      { key: 'MA_WEBSOCKET_MONITOR', kind: 'toggle', advanced: true },
      { key: 'MA_AUTO_SILENT_AUTH', kind: 'toggle', advanced: true },
      { key: 'DUPLICATE_DEVICE_CHECK', kind: 'toggle', advanced: true },
    ],
    after: ['sendspinTest'],
  },
  {
    id: 'audio',
    icon: 'audio',
    fields: [
      { key: 'PULSE_LATENCY_MSEC', kind: 'number', min: 0, max: 5000, step: 50, unit: 'ms' },
      { key: 'PREFER_SBC_CODEC', kind: 'toggle' },
      { key: 'SMOOTH_RESTART', kind: 'toggle', advanced: true },
      { key: 'DISABLE_PA_RESCUE_STREAMS', kind: 'toggle', advanced: true },
    ],
    after: ['latencyAssistant'],
  },
  {
    id: 'bluetooth',
    icon: 'bluetooth',
    before: ['adapters'],
    fields: [
      { key: 'BT_CHECK_INTERVAL', kind: 'number', min: 1, max: 300, unit: 's' },
      { key: 'BT_MAX_RECONNECT_FAILS', kind: 'number', min: 0 },
      { key: 'RSSI_BADGE', kind: 'toggle' },
      { key: 'BT_CHURN_THRESHOLD', kind: 'number', min: 0, advanced: true },
      { key: 'BT_CHURN_WINDOW', kind: 'number', min: 0, unit: 's', advanced: true },
      { key: 'EXPERIMENTAL_A2DP_SINK_RECOVERY_DANCE', kind: 'toggle', advanced: true },
      { key: 'EXPERIMENTAL_PA_MODULE_RELOAD', kind: 'toggle', advanced: true },
      { key: 'EXPERIMENTAL_ADAPTER_AUTO_RECOVERY', kind: 'toggle', advanced: true },
    ],
  },
  {
    id: 'homeAssistant',
    icon: 'home',
    before: ['haStatus'],
    fields: [
      {
        key: 'HA_INTEGRATION.mode',
        kind: 'select',
        options: ['off', 'mqtt', 'rest'],
        // ``enabled`` predates the mode selector; the bridge still reads it.
        onChange: (value, set) => set('HA_INTEGRATION.enabled', value !== 'off'),
      },
      { key: 'HA_INTEGRATION.mqtt.broker', kind: 'text', placeholder: 'auto', visible: (c) => haMode(c) === 'mqtt' },
      { key: 'HA_INTEGRATION.mqtt.port', kind: 'number', min: 1, max: 65535, visible: (c) => haMode(c) === 'mqtt' },
      { key: 'HA_INTEGRATION.mqtt.tls', kind: 'toggle', visible: (c) => haMode(c) === 'mqtt' },
      { key: 'HA_INTEGRATION.mqtt.username', kind: 'text', visible: (c) => haMode(c) === 'mqtt' },
      { key: 'HA_INTEGRATION.mqtt.password', kind: 'password', visible: (c) => haMode(c) === 'mqtt' },
      {
        key: 'HA_INTEGRATION.mqtt.discovery_prefix',
        kind: 'text',
        placeholder: 'homeassistant',
        advanced: true,
        visible: (c) => haMode(c) === 'mqtt',
      },
      { key: 'HA_INTEGRATION.mqtt.client_id', kind: 'text', advanced: true, visible: (c) => haMode(c) === 'mqtt' },
      { key: 'HA_INTEGRATION.rest.advertise_mdns', kind: 'toggle', visible: (c) => haMode(c) === 'rest' },
      { key: 'HA_INTEGRATION.rest.supervisor_pair', kind: 'toggle', visible: (c) => haMode(c) === 'rest' },
      {
        key: 'HA_INTEGRATION.rest.advertise_host',
        kind: 'text',
        placeholder: 'auto',
        advanced: true,
        visible: (c) => haMode(c) === 'rest',
      },
      {
        key: 'HA_INTEGRATION.rest.advertise_port',
        kind: 'number',
        min: 0,
        max: 65535,
        placeholder: '0',
        advanced: true,
        visible: (c) => haMode(c) === 'rest',
      },
      { key: 'HA_AREA_NAME_ASSIST_ENABLED', kind: 'toggle' },
    ],
    after: ['areaMap'],
  },
  {
    id: 'security',
    icon: 'shield',
    fields: [
      { key: 'AUTH_ENABLED', kind: 'toggle' },
      { key: 'SESSION_TIMEOUT_HOURS', kind: 'number', min: 1, unit: 'h' },
      { key: 'BRUTE_FORCE_PROTECTION', kind: 'toggle' },
      { key: 'BRUTE_FORCE_MAX_ATTEMPTS', kind: 'number', min: 1, visible: bruteForceOn },
      { key: 'BRUTE_FORCE_WINDOW_MINUTES', kind: 'number', min: 1, unit: 'min', visible: bruteForceOn },
      { key: 'BRUTE_FORCE_LOCKOUT_MINUTES', kind: 'number', min: 1, unit: 'min', visible: bruteForceOn },
      { key: 'TRUSTED_PROXIES', kind: 'list', advanced: true },
    ],
    after: ['password', 'tokens'],
  },
  {
    id: 'updates',
    icon: 'updates',
    fields: [
      { key: 'CHECK_UPDATES', kind: 'toggle' },
      { key: 'UPDATE_CHANNEL', kind: 'select', options: ['stable', 'rc', 'beta'] },
      { key: 'AUTO_UPDATE', kind: 'toggle' },
    ],
    after: ['updates'],
  },
  {
    id: 'guidance',
    icon: 'guidance',
    fields: [
      { key: 'STARTUP_BANNER_GRACE_SECONDS', kind: 'number', min: 0, unit: 's' },
      { key: 'RECOVERY_BANNER_GRACE_SECONDS', kind: 'number', min: 0, unit: 's' },
    ],
  },
  {
    id: 'backup',
    icon: 'backup',
    fields: [],
    after: ['backup'],
  },
]

/** Config keys a section component edits instead of a plain field. */
export const COMPONENT_OWNED_KEYS: Readonly<Record<string, SectionComponent | 'devices'>> = {
  BLUETOOTH_ADAPTERS: 'adapters',
  BLUETOOTH_DEVICES: 'devices',
  MA_API_URL: 'maConnection',
  AUTH_TOKENS: 'tokens',
  HA_ADAPTER_AREA_MAP: 'areaMap',
}

/** Keys the bridge manages itself (secrets, tokens, runtime state, schema version). */
export const MANAGED_KEYS: readonly string[] = [
  'AUTH_PASSWORD_HASH',
  'SECRET_KEY',
  'CONFIG_SCHEMA_VERSION',
  'LAST_SINKS',
  'LAST_VOLUMES',
  'MA_API_TOKEN',
  'MA_ACCESS_TOKEN',
  'MA_REFRESH_TOKEN',
  'MA_TOKEN_INSTANCE_HOSTNAME',
  'MA_TOKEN_LABEL',
  'MA_USERNAME',
  'MA_AUTH_PROVIDER',
  // Legacy switch kept in step with HA_INTEGRATION.mode by that field's onChange.
  'HA_INTEGRATION.enabled',
]

export const ALL_FIELDS: readonly FieldDef[] = SECTIONS.flatMap((s) => s.fields)
