<script setup lang="ts">
import { ref, computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useConfigStore } from '@/stores/config'
import { useNotificationStore } from '@/stores/notifications'
import { SbInput, SbToggle, SbButton, SbCard } from '@/kit'

const { t } = useI18n()
const configStore = useConfigStore()
const notifications = useNotificationStore()

const passwordEnabled = computed({
  get: () => !!(configStore.config as Record<string, unknown>)?.AUTH_PASSWORD_HASH,
  set: (v: boolean) => {
    if (!v) {
      configStore.updateField('AUTH_PASSWORD_HASH', '')
    }
  },
})

const currentPassword = ref('')
const newPassword = ref('')
const confirmPassword = ref('')

const passwordMismatch = computed(
  () => newPassword.value !== '' && confirmPassword.value !== '' && newPassword.value !== confirmPassword.value,
)

function handleChangePassword() {
  if (passwordMismatch.value || !newPassword.value) return
  // The backend hashes the password; we send the raw value for the API to process
  configStore.updateField('AUTH_PASSWORD', newPassword.value)
  currentPassword.value = ''
  newPassword.value = ''
  confirmPassword.value = ''
  notifications.info(t('config.save'))
}
</script>

<template>
  <div class="space-y-6 py-6">
    <SbCard>
      <template #header>
        <h3 class="text-lg font-semibold text-text-primary">{{ t('config.security') }}</h3>
      </template>
      <div class="space-y-5">
        <SbToggle
          v-model="passwordEnabled"
          :label="t('config.enablePassword')"
        />

        <div v-if="passwordEnabled" class="space-y-4 rounded-lg border border-gray-200 p-4 dark:border-gray-700">
          <SbInput
            v-model="currentPassword"
            :label="t('config.currentPassword')"
            type="password"
          />
          <SbInput
            v-model="newPassword"
            :label="t('config.newPassword')"
            type="password"
          />
          <SbInput
            v-model="confirmPassword"
            :label="t('config.confirmPassword')"
            type="password"
            :error="passwordMismatch ? t('config.passwordMismatch') : undefined"
          />
          <SbButton
            variant="primary"
            :disabled="passwordMismatch || !newPassword"
            @click="handleChangePassword"
          >
            {{ t('config.changePassword') }}
          </SbButton>
        </div>

        <p class="text-sm text-text-secondary italic">
          {{ t('config.haAuthInfo') }}
        </p>
      </div>
    </SbCard>
  </div>
</template>
