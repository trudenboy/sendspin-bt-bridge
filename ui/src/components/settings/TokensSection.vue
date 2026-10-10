<script setup lang="ts">
import { confirmDialog } from '@/composables/useConfirm'
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useNotificationStore } from '@/stores/notifications'
import { issueToken, listTokens, revokeToken } from '@/api/auth'
import { ApiError } from '@/api/client'
import { SbButton } from '@/kit'
import { Copy, Trash2 } from 'lucide-vue-next'
import type { Schemas } from '@/api/client'

const { t, locale } = useI18n()
const notifications = useNotificationStore()

const tokens = ref<Schemas['TokenRecord'][]>([])
const label = ref('')
const issued = ref<string | null>(null)
const busy = ref(false)

async function load() {
  try {
    tokens.value = (await listTokens()).tokens
  } catch {
    tokens.value = []
  }
}
onMounted(load)

async function create() {
  busy.value = true
  try {
    const res = await issueToken(label.value.trim() || 'api')
    issued.value = res.token
    label.value = ''
    await load()
  } catch (e) {
    notifications.error(e instanceof ApiError ? e.message : String(e))
  } finally {
    busy.value = false
  }
}

async function revoke(id: string) {
  const ok = await confirmDialog({
    title: t('settings.parts.tokens.revoke'),
    message: t('settings.parts.tokens.revokeConfirm'),
    confirmLabel: t('settings.parts.tokens.revoke'),
    danger: true,
  })
  if (!ok) return
  try {
    await revokeToken(id)
    await load()
  } catch (e) {
    notifications.error(e instanceof ApiError ? e.message : String(e))
  }
}

async function copy() {
  if (!issued.value) return
  try {
    await navigator.clipboard.writeText(issued.value)
    notifications.success(t('settings.parts.tokens.copied'))
  } catch {
    /* clipboard needs a secure context; the token stays visible to copy by hand */
  }
}

function when(value?: string | null) {
  return value ? new Date(value).toLocaleString(locale.value, { dateStyle: 'medium', timeStyle: 'short' }) : '—'
}
</script>

<template>
  <div class="py-3">
    <p class="text-sm font-medium text-text-primary">{{ t('settings.parts.tokens.label') }}</p>
    <p class="mt-0.5 text-[13px] text-text-secondary">{{ t('settings.parts.tokens.help') }}</p>

    <div v-if="issued" class="mt-3 rounded-(--radius-card) border border-warning/50 bg-warning/10 p-3">
      <p class="text-sm text-text-primary">{{ t('settings.parts.tokens.once') }}</p>
      <div class="mt-2 flex items-center gap-2">
        <code class="min-w-0 flex-1 truncate rounded bg-code-bg px-2 py-1 font-mono text-xs text-code-text">{{ issued }}</code>
        <SbButton variant="outline" size="sm" icon :title="t('settings.parts.tokens.copy')" @click="copy"><Copy /></SbButton>
      </div>
    </div>

    <ul v-if="tokens.length" class="mt-3 divide-y divide-border rounded-(--radius-card) border border-border">
      <li v-for="tok in tokens" :key="tok.id ?? ''" class="flex items-center justify-between gap-3 px-3 py-2.5">
        <div class="min-w-0">
          <p class="truncate text-sm font-medium text-text-primary">{{ tok.label || tok.id }}</p>
          <p class="text-xs text-text-secondary">
            {{ t('settings.parts.tokens.created', { when: when(tok.created) }) }} ·
            {{ t('settings.parts.tokens.lastUsed', { when: when(tok.last_used) }) }}
          </p>
        </div>
        <SbButton variant="ghost" size="sm" icon :title="t('settings.parts.tokens.revoke')" @click="tok.id && revoke(tok.id)">
          <Trash2 class="text-error" />
        </SbButton>
      </li>
    </ul>

    <form class="mt-3 flex gap-2 sm:max-w-md" @submit.prevent="create">
      <input
        v-model="label"
        maxlength="64"
        :placeholder="t('settings.parts.tokens.newLabel')"
        :aria-label="t('settings.parts.tokens.newLabel')"
        class="h-9 min-w-0 flex-1 rounded-(--radius-input) border border-border-strong bg-surface-card px-3 text-sm text-text-primary outline-none focus:border-primary focus:ring-2 focus:ring-primary/25"
      />
      <SbButton type="submit" variant="outline" :loading="busy">{{ t('settings.parts.tokens.create') }}</SbButton>
    </form>
  </div>
</template>
