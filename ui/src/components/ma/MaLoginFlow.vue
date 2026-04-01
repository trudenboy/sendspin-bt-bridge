<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useMaStore } from '@/stores/ma'
import { SbInput, SbButton, SbCard } from '@/kit'
import { Search, LogIn } from 'lucide-vue-next'

const { t } = useI18n()
const ma = useMaStore()

const serverUrl = ref('')
const token = ref('')
const error = ref('')
const loggingIn = ref(false)

async function discover() {
  error.value = ''
  await ma.discover()
}

async function doLogin() {
  error.value = ''
  loggingIn.value = true
  try {
    const result = await ma.login(token.value)
    if (!result.success) {
      error.value = t('ma.login.failed')
    }
  } catch {
    error.value = t('ma.login.failed')
  } finally {
    loggingIn.value = false
  }
}
</script>

<template>
  <SbCard>
    <template #header>
      <span>{{ t('ma.login.title') }}</span>
    </template>

    <div class="space-y-4">
      <!-- Step 1: Server URL -->
      <div class="space-y-2">
        <p class="text-sm font-medium text-text-primary">{{ t('ma.login.step1') }}</p>
        <div class="flex gap-2">
          <div class="flex-1">
            <SbInput
              v-model="serverUrl"
              :label="t('ma.login.serverUrl')"
              :placeholder="t('ma.login.serverUrlPlaceholder')"
              type="url"
            />
          </div>
          <SbButton
            variant="secondary"
            :loading="ma.discovering"
            class="mt-6"
            @click="discover"
          >
            <template #icon-left>
              <Search class="h-4 w-4" aria-hidden="true" />
            </template>
            {{ t('ma.login.discover') }}
          </SbButton>
        </div>
      </div>

      <!-- Step 2: Token -->
      <div class="space-y-2">
        <p class="text-sm font-medium text-text-primary">{{ t('ma.login.step2') }}</p>
        <SbInput
          v-model="token"
          :label="t('ma.login.token')"
          :placeholder="t('ma.login.tokenPlaceholder')"
          type="password"
        />
      </div>

      <!-- Error -->
      <p v-if="error" class="text-sm text-error" role="alert">{{ error }}</p>

      <!-- Login button -->
      <SbButton
        variant="primary"
        :loading="loggingIn"
        :disabled="!token"
        @click="doLogin"
      >
        <template #icon-left>
          <LogIn class="h-4 w-4" aria-hidden="true" />
        </template>
        {{ t('ma.login.submit') }}
      </SbButton>
    </div>
  </SbCard>
</template>
