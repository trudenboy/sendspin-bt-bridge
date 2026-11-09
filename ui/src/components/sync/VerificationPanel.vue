<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { SbButton } from '@/kit'
import { verifySession, type ListeningDevice } from '@/api/sync'
import type { SyncSession } from '@/api/types'
import { errorCode, useSyncStore } from '@/stores/sync'
import { formatMs } from '@/sync/format'
import PhoneCapture from './PhoneCapture.vue'
import ListeningCapture from './ListeningCapture.vue'

const props = defineProps<{ session: SyncSession; input?: ListeningDevice | null; disabled?: boolean }>()
const { t } = useI18n()
const sync = useSyncStore()
const busy = ref(false)
const error = ref('')
const phone = ref(false)
let disposed = false
onBeforeUnmount(() => { disposed = true })
const active = computed(() => props.session.verification?.state === 'recording')
const result = computed(() => props.session.verification)
const members = computed(() => props.session.members.filter((member) => member.adjustable && !props.session.departed.includes(member.ma_player_id)))

async function act(action: 'start' | 'finish' | 'cancel') {
  if (busy.value) return
  const sid = props.session.id
  busy.value = true
  error.value = ''
  try {
    if (action === 'start') {
      await sync.flushDelays()
      phone.value = !props.input
    }
    const next = await verifySession(sid, action)
    if (!disposed && props.session.id === sid) sync.applyEvent(next)
  } catch (e) {
    if (!disposed) error.value = t('sync.wizard.failed', { code: errorCode(e) })
  } finally {
    if (!disposed) busy.value = false
  }
}
</script>

<template>
  <section data-verification class="flex flex-col gap-3 rounded-(--radius-card) border border-border p-4">
    <p class="text-sm text-text-secondary">{{ t('sync.verification.help') }}</p>
    <template v-if="active">
      <ListeningCapture v-if="input && !phone" :session="session" :members="members" :input="input"
        @next="act('finish')" @back="act('cancel')" @cancel="act('cancel')" @restart="sync.signal('start')"
        @phone="phone = true" />
      <PhoneCapture v-else :session="session" :members="members" @next="act('finish')"
        @back="act('cancel')" @cancel="act('cancel')" @restart="sync.signal('start')" />
    </template>
    <template v-else>
      <p v-if="result?.state === 'complete'" data-verification-result role="status" class="text-sm text-text-primary">
        <span v-if="result.spread_ms != null">{{ t('sync.verification.measured', { ms: formatMs(result.spread_ms) }) }} </span>
        {{ t(result.passed ? 'sync.verification.passed' : 'sync.verification.needsWork') }}
      </p>
      <p v-if="result?.state === 'invalidated'" data-verification-invalidated role="status" class="text-sm text-warning-text">
        {{ t('sync.verification.changed') }}
      </p>
      <SbButton variant="outline" data-verify :loading="busy" :disabled="disabled || members.length < 2"
        @click="act('start')">{{ t('sync.verification.start') }}</SbButton>
    </template>
    <p v-if="error" role="alert" class="text-sm text-error">{{ error }}</p>
  </section>
</template>
