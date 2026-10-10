<script setup lang="ts">
import { computed, useId } from 'vue'
import { useI18n } from 'vue-i18n'
import { SbToggle } from '@/kit'
import SettingsRow from './SettingsRow.vue'
import { getPath, type FieldDef } from '@/settings/layout'
import { useConfigStore } from '@/stores/config'

const props = withDefaults(
  defineProps<{
    field: FieldDef
    /** i18n prefix for ``<key>.label|help|options`` (bridge settings by default). */
    i18nBase?: string
    /** Read/write another document than the bridge config (a device entry). */
    read?: (key: string) => unknown
    write?: (key: string, value: unknown) => void
    /** Choices for ``optionsFrom`` selects. */
    dynamicOptions?: readonly { value: string; label: string }[]
  }>(),
  { i18nBase: 'settings.fields', read: undefined, write: undefined, dynamicOptions: undefined },
)

const { t, te } = useI18n()
const configStore = useConfigStore()

const id = `setting-${useId()}`
const helpId = `${id}-help`
const base = computed(() => `${props.i18nBase}.${props.field.key}`)
const label = computed(() => t(`${base.value}.label`))
const help = computed(() => t(`${base.value}.help`))

const value = computed(() => {
  const v = props.read ? props.read(props.field.key) : getPath(configStore.config as Record<string, unknown>, props.field.key)
  return v === undefined ? props.field.default : v
})

function write(key: string, v: unknown) {
  if (props.write) props.write(key, v)
  else configStore.updateField(key, v)
}

function set(v: unknown) {
  write(props.field.key, v)
  props.field.onChange?.(v, write)
}

const choices = computed(() =>
  props.field.optionsFrom
    ? (props.dynamicOptions ?? [])
    : (props.field.options ?? []).map((o) => ({ value: o, label: optionLabel(o) })),
)

function optionLabel(option: string) {
  const key = `${base.value}.options.${option}`
  return te(key) ? t(key) : option
}

/** Differs from what is saved (bridge settings only; device panels track their own). */
const changed = computed(() => {
  if (props.read) return false
  const saved = getPath(configStore.originalConfig as Record<string, unknown>, props.field.key)
  const now = getPath(configStore.config as Record<string, unknown>, props.field.key)
  return JSON.stringify(saved ?? null) !== JSON.stringify(now ?? null)
})

const error = computed(() => (props.read ? undefined : configStore.validationErrors[props.field.key]))

const numberText = computed(() => (value.value == null ? '' : String(value.value)))
function onNumber(raw: string) {
  if (raw.trim() === '') {
    set(props.field.nullable ? null : props.field.min ?? 0)
    return
  }
  const n = Number(raw)
  if (Number.isFinite(n)) set(n)
}

const listText = computed(() => (Array.isArray(value.value) ? (value.value as string[]).join('\n') : ''))
function onList(raw: string) {
  set(
    raw
      .split(/[\n,]/)
      .map((s) => s.trim())
      .filter(Boolean),
  )
}

const inputClass =
  'h-9 w-full rounded-(--radius-input) border border-border-strong bg-surface-card px-3 text-sm text-text-primary outline-none transition-colors placeholder:text-text-disabled focus:border-primary focus:ring-2 focus:ring-primary/25'
</script>

<template>
  <SettingsRow :label="label" :help="help" :for="id" :help-id="helpId" :wide="field.kind === 'list'" :changed="changed">
    <SbToggle
      v-if="field.kind === 'toggle'"
      :id="id"
      :model-value="value === true"
      :aria-describedby="helpId"
      @update:model-value="set"
    />

    <select
      v-else-if="field.kind === 'select'"
      :id="id"
      :class="[inputClass, 'min-w-44 cursor-pointer pr-8']"
      :value="value ?? ''"
      :aria-describedby="helpId"
      @change="set(($event.target as HTMLSelectElement).value)"
    >
      <option v-if="field.nullable" value="">{{ t('settings.auto') }}</option>
      <option v-for="o in choices" :key="o.value" :value="o.value">{{ o.label }}</option>
    </select>

    <div v-else-if="field.kind === 'number'" class="flex items-center justify-end gap-2">
      <input
        :id="id"
        type="number"
        inputmode="numeric"
        :class="[inputClass, 'w-32 text-right tabular-nums']"
        :value="numberText"
        :min="field.min"
        :max="field.max"
        :step="field.step ?? 1"
        :placeholder="field.placeholder ?? (field.nullable ? t('settings.auto') : '')"
        :aria-describedby="helpId"
        :aria-invalid="!!error"
        @change="onNumber(($event.target as HTMLInputElement).value)"
      />
      <span class="w-8 text-sm text-text-secondary" :aria-hidden="!field.unit">{{ field.unit ? t(`settings.units.${field.unit}`) : '' }}</span>
    </div>

    <textarea
      v-else-if="field.kind === 'list'"
      :id="id"
      :class="[inputClass, 'h-24 py-2 font-mono']"
      :value="listText"
      spellcheck="false"
      :aria-describedby="helpId"
      @change="onList(($event.target as HTMLTextAreaElement).value)"
    />

    <input
      v-else
      :id="id"
      :type="field.kind === 'password' ? 'password' : 'text'"
      :autocomplete="field.kind === 'password' ? 'new-password' : 'off'"
      :class="[inputClass, 'sm:w-64 sm:max-w-full']"
      :value="value === '***REDACTED***' ? '' : (value ?? '')"
      :placeholder="value === '***REDACTED***' ? '••••••••' : field.placeholder"
      :aria-describedby="helpId"
      :aria-invalid="!!error"
      @change="set(($event.target as HTMLInputElement).value)"
    />

    <p v-if="error" class="mt-1 text-xs text-error" role="alert">{{ error }}</p>
  </SettingsRow>
</template>
