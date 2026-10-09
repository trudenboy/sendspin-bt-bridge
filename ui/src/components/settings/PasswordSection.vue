<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useConfigStore } from '@/stores/config'
import { useNotificationStore } from '@/stores/notifications'
import { setPassword } from '@/api/auth'
import { ApiError } from '@/api/client'
import { SbButton, SbInput } from '@/kit'
import SettingsRow from './SettingsRow.vue'

const { t } = useI18n()
const configStore = useConfigStore()
const notifications = useNotificationStore()

const passwordSet = computed(() => (configStore.config as Record<string, unknown> | null)?._password_set === true)
const open = ref(false)
const current = ref('')
const next = ref('')
const confirm = ref('')
const saving = ref(false)
const error = ref('')

const mismatch = computed(() => confirm.value !== '' && next.value !== confirm.value)
const tooShort = computed(() => next.value !== '' && next.value.length < 8)
const canSave = computed(() => next.value.length >= 8 && !mismatch.value && (!passwordSet.value || current.value !== ''))

async function save() {
  saving.value = true
  error.value = ''
  try {
    await setPassword(next.value, current.value)
    // The settings document must not resend an old hash state.
    ;(configStore.config as Record<string, unknown>)._password_set = true
    current.value = next.value = confirm.value = ''
    open.value = false
    notifications.success(t('settings.parts.password.changed'))
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : String(e)
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div>
    <SettingsRow
      :label="t('settings.parts.password.label')"
      :help="passwordSet ? t('settings.parts.password.set') : t('settings.parts.password.notSet')"
    >
      <SbButton variant="outline" size="sm" @click="open = !open">
        {{ passwordSet ? t('settings.parts.password.change') : t('settings.parts.password.create') }}
      </SbButton>
    </SettingsRow>
    <form v-if="open" class="grid gap-3 pb-4 sm:max-w-md" @submit.prevent="save">
      <SbInput v-if="passwordSet" v-model="current" type="password" :label="t('settings.parts.password.current')" />
      <SbInput
        v-model="next"
        type="password"
        :label="t('settings.parts.password.new')"
        :error="tooShort ? t('settings.parts.password.tooShort') : undefined"
      />
      <SbInput
        v-model="confirm"
        type="password"
        :label="t('settings.parts.password.confirm')"
        :error="mismatch ? t('config.passwordMismatch') : undefined"
      />
      <p v-if="error" class="text-sm text-error" role="alert">{{ error }}</p>
      <div>
        <SbButton type="submit" :disabled="!canSave" :loading="saving">{{ t('settings.parts.password.save') }}</SbButton>
      </div>
    </form>
  </div>
</template>
