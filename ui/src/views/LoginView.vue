<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useAuthStore } from '@/stores/auth'
import { SbCard, SbInput, SbButton } from '@/kit'
import { LogIn } from 'lucide-vue-next'

const { t } = useI18n()
const auth = useAuthStore()
const router = useRouter()

const password = ref('')
const error = ref('')
const loading = ref(false)

async function doLogin() {
  error.value = ''
  loading.value = true
  try {
    const result = await auth.login(password.value)
    if (result.success) {
      router.push({ name: 'dashboard' })
    } else {
      error.value = result.error ?? t('login.failed')
    }
  } catch {
    error.value = t('login.failed')
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

      <!-- HA users message -->
      <div v-if="auth.haUser" class="mb-4 rounded-lg bg-success/10 px-4 py-3 text-sm text-success">
        {{ t('login.haAuthenticated') }}
      </div>

      <form v-else class="space-y-4" @submit.prevent="doLogin">
        <SbInput
          v-model="password"
          :label="t('login.password')"
          :placeholder="t('login.passwordPlaceholder')"
          type="password"
          required
          :error="error"
        />

        <SbButton
          variant="primary"
          :loading="loading"
          :disabled="!password"
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
