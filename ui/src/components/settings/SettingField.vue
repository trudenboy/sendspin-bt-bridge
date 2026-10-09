<script setup lang="ts">
import { computed, useId } from 'vue'
import { useI18n } from 'vue-i18n'
import { SbToggle } from '@/kit'
import SettingsRow from './SettingsRow.vue'
import { getPath, type FieldDef } from '@/settings/layout'
import { useConfigStore } from '@/stores/config'

const props = defineProps<{ field: FieldDef }>()

const { t, te } = useI18n()
const configStore = useConfigStore()

const id = `setting-${useId()}`
const helpId = `${id}-help`
const base = computed(() => `settings.fields.${props.field.key}`)
const label = computed(() => t(`${base.value}.label`))
const help = computed(() => t(`${base.value}.help`))

const value = computed(() => getPath(configStore.config as Record<string, unknown>, props.field.key))

function set(v: unknown) {
  configStore.updateField(props.field.key, v)
  props.field.onChange?.(v, (k, val) => configStore.updateField(k, val))
}

function optionLabel(option: string) {
  const key = `${base.value}.options.${option}`
  return te(key) ? t(key) : option
}

const error = computed(() => configStore.validationErrors[props.field.key])

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
  <SettingsRow :label="label" :help="help" :for="id" :help-id="helpId" :wide="field.kind === 'list'">
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
      <option v-for="o in field.options" :key="o" :value="o">{{ optionLabel(o) }}</option>
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
      :class="[inputClass, 'sm:w-64']"
      :value="value === '***REDACTED***' ? '' : (value ?? '')"
      :placeholder="value === '***REDACTED***' ? '••••••••' : field.placeholder"
      :aria-describedby="helpId"
      :aria-invalid="!!error"
      @change="set(($event.target as HTMLInputElement).value)"
    />

    <p v-if="error" class="mt-1 text-xs text-error" role="alert">{{ error }}</p>
  </SettingsRow>
</template>
