<script setup lang="ts">
import { onUnmounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useMaStore } from '@/stores/ma'
import { useNotificationStore } from '@/stores/notifications'
import { apiUrl, ApiError } from '@/api/client'
import { SbInput, SbButton, SbCard } from '@/kit'
import { Search, LogIn, Home } from 'lucide-vue-next'

const { t } = useI18n()
const ma = useMaStore()
const notifications = useNotificationStore()

const serverUrl = ref('')
const username = ref('')
const password = ref('')
const error = ref('')
const loggingIn = ref(false)

async function discover() {
  error.value = ''
  try {
    const servers = await ma.discover()
    if (servers[0]?.url) serverUrl.value = servers[0].url
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : String(e)
  }
}

async function doLogin() {
  error.value = ''
  loggingIn.value = true
  try {
    const result = await ma.login(serverUrl.value, username.value, password.value)
    password.value = ''
    notifications.success(t('ma.login.connected', { url: result.url }))
    await ma.fetchConnection()
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : t('ma.login.failed')
  } finally {
    loggingIn.value = false
  }
}

/** Music Assistant behind Home Assistant: the bridge's popup runs HA's login (with MFA). */
function onPopupMessage(event: MessageEvent) {
  if (event.origin !== window.location.origin || event.data?.type !== 'ma-ha-auth-done') return
  notifications.success(t('ma.login.connected', { url: event.data.url ?? '' }))
  void ma.fetchConnection()
}

function signInWithHa() {
  const url = apiUrl(`/api/v1/music-assistant/session/ha-auth-page?ma_url=${encodeURIComponent(serverUrl.value)}`)
  window.open(url, 'ma-ha-auth', 'width=420,height=640')
}

window.addEventListener('message', onPopupMessage)
onUnmounted(() => window.removeEventListener('message', onPopupMessage))
</script>

<template>
  <SbCard>
    <template #header>
      <span>{{ t('ma.login.title') }}</span>
    </template>

    <div class="space-y-4">
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
          <SbButton variant="secondary" :loading="ma.discovering" class="mt-6" @click="discover">
            <template #icon-left>
              <Search class="h-4 w-4" aria-hidden="true" />
            </template>
            {{ t('ma.login.discover') }}
          </SbButton>
        </div>
      </div>

      <form class="space-y-2" @submit.prevent="doLogin">
        <p class="text-sm font-medium text-text-primary">{{ t('ma.login.step2') }}</p>
        <SbInput v-model="username" :label="t('ma.login.username')" autocomplete="username" />
        <SbInput v-model="password" :label="t('ma.login.password')" type="password" autocomplete="current-password" />
        <p v-if="error" class="text-sm text-error" role="alert">{{ error }}</p>
        <div class="flex flex-wrap gap-2">
          <SbButton variant="primary" type="submit" :loading="loggingIn" :disabled="!username || !password">
            <template #icon-left>
              <LogIn class="h-4 w-4" aria-hidden="true" />
            </template>
            {{ t('ma.login.submit') }}
          </SbButton>
          <SbButton variant="secondary" :disabled="!serverUrl" @click="signInWithHa">
            <template #icon-left>
              <Home class="h-4 w-4" aria-hidden="true" />
            </template>
            {{ t('ma.login.withHa') }}
          </SbButton>
        </div>
      </form>
    </div>
  </SbCard>
</template>
