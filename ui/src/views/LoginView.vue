<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useAuthStore } from '@/stores/auth'
import { ApiError } from '@/api/client'
import { SbCard, SbInput, SbButton } from '@/kit'
import { LogIn } from 'lucide-vue-next'
import type { AuthMethod } from '@/api/types'

const { t } = useI18n()
const auth = useAuthStore()
const router = useRouter()

const method = ref<AuthMethod>('password')
const username = ref('')
const password = ref('')
const code = ref('')
const error = ref('')
const loading = ref(false)

watch(
  () => auth.methods,
  (methods) => {
    if (methods.length && !methods.includes(method.value)) method.value = methods[0]!
  },
  { immediate: true },
)

const needsUsername = computed(() => method.value !== 'password')
const awaitingCode = computed(() => auth.pendingFlow !== null)

function explain(e: unknown) {
  return e instanceof ApiError ? e.message : t('login.failed')
}

async function doLogin() {
  error.value = ''
  loading.value = true
  try {
    const result = await auth.login(method.value, password.value, username.value)
    if (result.status === 'signed_in') await router.push({ name: 'dashboard' })
  } catch (e) {
    error.value = explain(e)
  } finally {
    loading.value = false
  }
}

async function doCode() {
  error.value = ''
  loading.value = true
  try {
    const result = await auth.submitCode(code.value)
    if (result.status === 'signed_in') await router.push({ name: 'dashboard' })
    else error.value = result.message || t('login.failed')
  } catch (e) {
    error.value = explain(e)
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="flex min-h-[60vh] items-center justify-center">
    <SbCard class="w-full max-w-sm">
      <template #header>
        <span class="text-center text-xl font-bold">{{ t('login.title') }}</span>
      </template>

      <form v-if="awaitingCode" class="space-y-4" @submit.prevent="doCode">
        <p class="text-sm text-text-secondary">
          {{ t('login.codePrompt', { module: auth.pendingFlow?.moduleName ?? 'authenticator app' }) }}
        </p>
        <SbInput v-model="code" :label="t('login.code')" inputmode="numeric" autocomplete="one-time-code" required :error="error" />
        <SbButton variant="primary" :loading="loading" :disabled="!code" class="w-full" type="submit">
          {{ t('login.codeSubmit') }}
        </SbButton>
      </form>

      <form v-else class="space-y-4" @submit.prevent="doLogin">
        <div v-if="auth.methods.length > 1" class="flex flex-col gap-1.5 text-sm">
          <label v-for="m in auth.methods" :key="m" class="flex items-center gap-2">
            <input v-model="method" type="radio" :value="m" class="accent-primary" />
            {{ t(`login.method.${m}`) }}
          </label>
        </div>

        <SbInput v-if="needsUsername" v-model="username" :label="t('login.username')" autocomplete="username" required />
        <SbInput
          v-model="password"
          :label="t('login.password')"
          :placeholder="t('login.passwordPlaceholder')"
          type="password"
          autocomplete="current-password"
          required
          :error="error"
        />

        <SbButton
          variant="primary"
          :loading="loading"
          :disabled="!password || (needsUsername && !username)"
          class="w-full"
          type="submit"
        >
          <template #icon-left>
            <LogIn class="h-4 w-4" aria-hidden="true" />
          </template>
          {{ t('login.submit') }}
        </SbButton>
      </form>
    </SbCard>
  </div>
</template>
