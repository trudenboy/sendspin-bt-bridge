<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useUpdateStore } from '@/stores/update'
import { useBridgeStore } from '@/stores/bridge'
import SbDialog from '@/kit/SbDialog.vue'
import SbButton from '@/kit/SbButton.vue'
import SbBadge from '@/kit/SbBadge.vue'
import { ArrowUpCircle, ExternalLink, RefreshCw, Terminal, Copy } from 'lucide-vue-next'

const CHANNELS = ['stable', 'rc', 'beta'] as const

const { t } = useI18n()
const update = useUpdateStore()
const bridge = useBridgeStore()

const selectedChannel = ref(update.channel)

watch(() => update.channel, (ch) => {
  selectedChannel.value = ch
})

const channelTone = computed(() => {
  const map: Record<string, 'success' | 'warning' | 'info'> = {
    stable: 'success',
    rc: 'warning',
    beta: 'info',
  }
  return map[update.channel] ?? 'info'
})

const releaseNotesFormatted = computed(() => {
  const raw = update.releaseNotes
  if (!raw) return null
  return raw
    .replace(/^#{1,3}\s+/gm, '')
    .replace(/\*\*(.*?)\*\*/g, '$1')
    .trim()
    .slice(0, 2000)
})

function checkWithChannel() {
  update.checkForUpdates(selectedChannel.value)
}

function copyCommand() {
  if (update.info?.command) {
    navigator.clipboard.writeText(update.info.command)
  }
}

function openRelease() {
  if (update.info?.url) {
    window.open(update.info.url, '_blank', 'noopener')
  }
}

function openHaAddon() {
  if (update.info?.delivery_slug) {
    window.open(`/hassio/addon/${update.info.delivery_slug}/info`, '_blank')
  }
}
</script>

<template>
  <SbDialog v-model="update.showDialog" :title="t('update.title')" size="lg">
    <div class="space-y-5">
      <!-- Channel selector -->
      <div class="flex items-center gap-3">
        <span class="text-sm text-text-secondary">{{ t('update.channel') }}</span>
        <div class="flex rounded-lg border border-border">
          <button
            v-for="ch in CHANNELS"
            :key="ch"
            type="button"
            class="px-3 py-1.5 text-xs font-medium transition-colors first:rounded-l-lg last:rounded-r-lg"
            :class="
              selectedChannel === ch
                ? 'bg-primary text-white'
                : 'text-text-secondary hover:bg-surface-secondary'
            "
            @click="selectedChannel = ch"
          >
            {{ ch.toUpperCase() }}
          </button>
        </div>
        <SbButton
          v-if="selectedChannel !== update.channel"
          variant="primary"
          size="sm"
          :loading="update.checking"
          @click="checkWithChannel"
        >
          <template #icon-left><RefreshCw class="h-3.5 w-3.5" /></template>
          {{ t('update.checkChannel') }}
        </SbButton>
      </div>

      <!-- Version comparison -->
      <div v-if="update.latestVersion" class="flex items-center gap-3 rounded-lg bg-surface-secondary p-4">
        <ArrowUpCircle class="h-8 w-8 shrink-0 text-primary" />
        <div class="min-w-0">
          <p class="text-sm text-text-secondary">{{ t('update.versionCompare') }}</p>
          <p class="mt-1 text-lg font-semibold text-text-primary">
            <span class="text-text-secondary">v{{ bridge.version }}</span>
            <span class="mx-2 text-text-secondary">→</span>
            <span class="text-primary">v{{ update.latestVersion }}</span>
          </p>
          <div class="mt-1.5 flex items-center gap-2">
            <SbBadge :tone="channelTone" size="sm">
              {{ update.channel.toUpperCase() }}
            </SbBadge>
            <span
              v-if="update.info?.channel_warning"
              class="text-xs text-warning"
            >
              {{ update.info.channel_warning }}
            </span>
          </div>
        </div>
      </div>

      <!-- No update available -->
      <div v-else-if="!update.checking && !update.loading" class="rounded-lg bg-surface-secondary p-4 text-center text-sm text-text-secondary">
        {{ t('update.upToDate') }}
      </div>

      <!-- Checking spinner -->
      <div v-if="update.checking" class="py-4 text-center text-sm text-text-secondary">
        {{ t('update.checking') }}
      </div>

      <!-- Platform instructions -->
      <div v-if="update.info?.instructions" class="space-y-2">
        <h3 class="text-sm font-medium text-text-primary">{{ t('update.instructions') }}</h3>
        <p class="text-sm text-text-secondary">{{ update.info.instructions }}</p>

        <!-- Docker command -->
        <div
          v-if="update.info.command"
          class="group relative rounded-md bg-gray-900 p-3 font-mono text-xs text-green-400"
        >
          <pre class="overflow-x-auto whitespace-pre-wrap">{{ update.info.command }}</pre>
          <button
            class="absolute top-2 right-2 rounded p-1 text-gray-500 opacity-0 transition-opacity hover:text-gray-300 group-hover:opacity-100"
            :title="t('update.copyCommand')"
            @click="copyCommand"
          >
            <Copy class="h-4 w-4" />
          </button>
        </div>
      </div>

      <!-- Release notes -->
      <div v-if="releaseNotesFormatted" class="space-y-2">
        <h3 class="text-sm font-medium text-text-primary">{{ t('update.releaseNotes') }}</h3>
        <div
          class="max-h-48 overflow-y-auto rounded-lg border border-surface-secondary p-3 text-sm text-text-secondary"
        >
          <pre class="whitespace-pre-wrap font-sans">{{ releaseNotesFormatted }}</pre>
        </div>
      </div>

      <!-- Error -->
      <p v-if="update.error" class="text-sm text-error">{{ update.error }}</p>
    </div>

    <template #footer>
      <SbButton variant="ghost" size="sm" @click="checkWithChannel">
        <template #icon-left><RefreshCw class="h-4 w-4" /></template>
        {{ t('update.recheck') }}
      </SbButton>

      <SbButton
        v-if="update.info?.url"
        variant="ghost"
        size="sm"
        @click="openRelease"
      >
        <template #icon-left><ExternalLink class="h-4 w-4" /></template>
        {{ t('update.viewRelease') }}
      </SbButton>

      <div class="flex-1" />

      <!-- Platform-specific action -->
      <SbButton
        v-if="update.updateMethod === 'one_click'"
        variant="primary"
        size="sm"
        :loading="update.applying"
        @click="update.doApplyUpdate()"
      >
        <template #icon-left><Terminal class="h-4 w-4" /></template>
        {{ t('update.applyNow') }}
      </SbButton>

      <SbButton
        v-else-if="update.updateMethod === 'ha_store' && update.info?.delivery_slug"
        variant="primary"
        size="sm"
        @click="openHaAddon"
      >
        <template #icon-left><ExternalLink class="h-4 w-4" /></template>
        {{ t('update.openHa') }}
      </SbButton>

      <SbButton
        v-else
        variant="secondary"
        size="sm"
        @click="update.showDialog = false"
      >
        {{ t('common.close') }}
      </SbButton>
    </template>
  </SbDialog>
</template>
