<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useNotificationStore } from '@/stores/notifications'
import { useConfigStore } from '@/stores/config'
import { getTelemetry, listHooks, addHook, removeHook, type Hook, type Telemetry } from '@/api/system'
import { downloadBugreport, downloadLogs, downloadTimelineCsv } from '@/api/diagnostics'
import { ApiError } from '@/api/client'
import { SbBadge, SbButton, SbCard } from '@/kit'
import SettingsRow from '@/components/settings/SettingsRow.vue'
import { Download, Trash2 } from 'lucide-vue-next'

const { t } = useI18n()
const notifications = useNotificationStore()
const configStore = useConfigStore()

const telemetry = ref<Telemetry | null>(null)
const hooks = ref<Hook[]>([])
const url = ref('')
const categories = ref('')
const adding = ref(false)

async function load() {
  try {
    ;[telemetry.value, hooks.value] = await Promise.all([getTelemetry(), listHooks()])
  } catch {
    /* the cards say what is missing */
  }
}
onMounted(load)

function uptime(seconds?: number) {
  if (seconds == null) return '—'
  const d = Math.floor(seconds / 86400)
  const h = Math.floor((seconds % 86400) / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${m}m`
}

const facts = computed(() => {
  const b = telemetry.value?.bridge ?? {}
  return [
    [t('system.uptime'), uptime(b.uptime_seconds)],
    [t('system.memory'), b.process_rss_mb != null ? `${b.process_rss_mb.toFixed(0)} MB` : '—'],
    [t('system.platform'), [b.arch, b.kernel].filter(Boolean).join(' · ') || '—'],
    [t('system.python'), b.python?.split(' ')[0] ?? '—'],
    [t('system.audio'), b.audio_server?.split('\n')[0] ?? '—'],
    [t('system.bluez'), b.bluez?.replace('bluetoothctl: ', '') ?? '—'],
  ] as const
})

async function add() {
  adding.value = true
  try {
    const list = categories.value
      .split(',')
      .map((c) => c.trim())
      .filter(Boolean)
    await addHook(url.value.trim(), list)
    url.value = ''
    categories.value = ''
    await load()
  } catch (e) {
    notifications.error(e instanceof ApiError ? e.message : String(e))
  } finally {
    adding.value = false
  }
}

async function remove(id: string) {
  try {
    await removeHook(id)
    await load()
  } catch (e) {
    notifications.error(e instanceof ApiError ? e.message : String(e))
  }
}

const downloads = computed(() => [
  { key: 'report', run: downloadBugreport },
  { key: 'logs', run: downloadLogs },
  { key: 'timeline', run: downloadTimelineCsv },
  { key: 'config', run: () => configStore.downloadConfig() },
])
</script>

<template>
  <div class="grid gap-6 lg:grid-cols-2">
    <SbCard>
      <template #header>{{ t('system.bridge') }}</template>
      <dl class="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 text-sm">
        <template v-for="[label, value] in facts" :key="label">
          <dt class="text-text-secondary">{{ label }}</dt>
          <dd class="min-w-0 truncate text-text-primary" :title="value">{{ value }}</dd>
        </template>
      </dl>
    </SbCard>

    <SbCard>
      <template #header>{{ t('system.downloads') }}</template>
      <div class="divide-y divide-border">
        <SettingsRow
          v-for="d in downloads"
          :key="d.key"
          :label="t(`system.download.${d.key}.label`)"
          :help="t(`system.download.${d.key}.help`)"
        >
          <SbButton variant="outline" size="sm" icon :title="t('system.downloadAction')" @click="d.run()"><Download /></SbButton>
        </SettingsRow>
      </div>
    </SbCard>

    <SbCard class="lg:col-span-2">
      <template #header>{{ t('system.players') }}</template>
      <p v-if="!telemetry?.subprocesses.length" class="text-sm text-text-secondary">{{ t('system.noPlayers') }}</p>
      <table v-else class="w-full text-left text-sm">
        <thead class="text-xs text-text-secondary">
          <tr>
            <th class="py-1.5 font-medium">{{ t('system.player') }}</th>
            <th class="py-1.5 font-medium">PID</th>
            <th class="py-1.5 font-medium">{{ t('system.memory') }}</th>
            <th class="py-1.5 font-medium">{{ t('system.restarts') }}</th>
            <th class="py-1.5 font-medium">{{ t('system.state') }}</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-border">
          <tr v-for="p in telemetry.subprocesses" :key="p.name">
            <td class="py-2 pr-3 text-text-primary">{{ p.name }}</td>
            <td class="py-2 pr-3 font-mono text-xs text-text-secondary">{{ p.pid ?? '—' }}</td>
            <td class="py-2 pr-3 tabular-nums text-text-secondary">{{ p.process_rss_mb != null ? `${p.process_rss_mb.toFixed(0)} MB` : '—' }}</td>
            <td class="py-2 pr-3 tabular-nums text-text-secondary">{{ p.zombie_restarts ?? 0 }}</td>
            <td class="py-2">
              <SbBadge :tone="p.alive ? (p.reconnecting ? 'warning' : 'success') : 'error'" size="sm" dot :title="p.last_error ?? undefined">
                {{ p.alive ? (p.reconnecting ? t('system.reconnecting') : t('system.running')) : t('system.stopped') }}
              </SbBadge>
            </td>
          </tr>
        </tbody>
      </table>
    </SbCard>

    <SbCard class="lg:col-span-2">
      <template #header>{{ t('system.hooks.title') }}</template>
      <p class="text-[13px] text-text-secondary">{{ t('system.hooks.help') }}</p>
      <ul v-if="hooks.length" class="mt-3 divide-y divide-border rounded-(--radius-card) border border-border">
        <li v-for="h in hooks" :key="h.id" class="flex items-center gap-3 px-3 py-2.5">
          <div class="min-w-0 flex-1">
            <p class="truncate font-mono text-xs text-text-primary">{{ h.url }}</p>
            <p class="text-xs text-text-secondary">
              {{ h.categories.length ? h.categories.join(', ') : t('system.hooks.allEvents') }} ·
              {{ t('system.hooks.counts', { ok: h.success_count, failed: h.failure_count }) }}
              <span v-if="h.last_error" class="text-error"> · {{ h.last_error }}</span>
            </p>
          </div>
          <SbButton variant="ghost" size="sm" icon :title="t('system.hooks.remove')" @click="remove(h.id)"><Trash2 class="text-error" /></SbButton>
        </li>
      </ul>
      <form class="mt-3 grid gap-2 sm:grid-cols-[1fr_12rem_auto]" @submit.prevent="add">
        <input
          v-model="url"
          type="url"
          required
          placeholder="https://example.local/hook"
          :aria-label="t('system.hooks.url')"
          class="h-9 rounded-(--radius-input) border border-border-strong bg-surface-card px-3 text-sm text-text-primary outline-none focus:border-primary focus:ring-2 focus:ring-primary/25"
        />
        <input
          v-model="categories"
          :placeholder="t('system.hooks.categories')"
          :aria-label="t('system.hooks.categories')"
          class="h-9 rounded-(--radius-input) border border-border-strong bg-surface-card px-3 text-sm text-text-primary outline-none focus:border-primary focus:ring-2 focus:ring-primary/25"
        />
        <SbButton type="submit" variant="outline" :loading="adding">{{ t('system.hooks.add') }}</SbButton>
      </form>
    </SbCard>
  </div>
</template>
