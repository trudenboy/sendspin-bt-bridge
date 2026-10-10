<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { useTheme } from '@/composables/useTheme'
import { useBridgeStore } from '@/stores/bridge'
import { useUpdateStore } from '@/stores/update'
import { SbDropdown, SbDropdownItem } from '@/kit'
import BugReportDialog from '@/components/BugReportDialog.vue'
import { CircleHelp, SlidersHorizontal, ArrowUpCircle } from 'lucide-vue-next'

const { t, locale } = useI18n()
const router = useRouter()
const { mode } = useTheme()
const bridge = useBridgeStore()
const update = useUpdateStore()

const GITHUB_URL = 'https://github.com/trudenboy/sendspin-bt-bridge'
const DOCS_URL = 'https://trudenboy.github.io/sendspin-bt-bridge'

const bugReportOpen = ref(false)
const bridgeName = computed(() => bridge.bridge?.name || t('app.title'))

/** The bridge's own one-line verdict (guidance header), as a pill that opens diagnostics. */
const status = computed(() => {
  const header = (bridge.bridge?.guidance as { header_status?: { tone?: string; label?: string } } | null)?.header_status
  return header?.label ? { tone: header.tone ?? 'neutral', label: header.label } : null
})
const statusClass: Record<string, string> = {
  success: 'tone-success',
  warning: 'tone-warning',
  error: 'tone-error',
  info: 'tone-info',
  neutral: 'tone-neutral',
}

function setLocale(next: string) {
  locale.value = next
  try {
    localStorage.setItem('sendspin-ui:locale', next)
  } catch {
    /* remembered for this visit only */
  }
}

function open(url: string) {
  window.open(url, '_blank', 'noopener,noreferrer')
}

onMounted(() => {
  update.fetchInfo()
})

const iconButton =
  'inline-flex size-10 items-center justify-center rounded-full text-text-secondary transition-colors hover:bg-surface-secondary hover:text-text-primary'
</script>

<template>
  <header class="fixed top-0 right-0 left-0 z-30 border-b border-border bg-surface-card">
    <div class="flex h-16 items-center gap-2 px-4 sm:gap-3 sm:px-6">
      <router-link to="/" class="flex min-w-0 shrink items-center gap-2.5">
        <img src="/bridge-logo.svg" alt="" class="size-8 shrink-0" width="32" height="32" />
        <span class="truncate text-base font-medium text-text-primary sm:text-lg">{{ bridgeName }}</span>
      </router-link>

      <div class="ml-auto flex shrink-0 items-center gap-1 sm:gap-2">
        <button
          v-if="status"
          type="button"
          class="hidden max-w-64 items-center gap-1.5 truncate rounded-full px-3 py-1 text-xs font-medium md:inline-flex"
          :class="statusClass[status.tone] ?? 'tone-neutral'"
          :title="t('header.openDiagnostics')"
          @click="router.push('/diagnostics')"
        >
          <span class="size-1.5 shrink-0 rounded-full bg-current" aria-hidden="true" />
          <span class="truncate">{{ status.label }}</span>
        </button>

        <button
          v-if="update.updateAvailable"
          type="button"
          class="inline-flex items-center gap-1.5 rounded-full tone-info px-3 py-1 text-xs font-medium"
          :title="t('update.available')"
          @click="update.openDialog()"
        >
          <ArrowUpCircle class="size-3.5" aria-hidden="true" />
          <span class="hidden sm:inline">{{ t('update.badge', { version: update.latestVersion }) }}</span>
        </button>

        <SbDropdown align="right">
          <template #trigger>
            <button type="button" :class="iconButton" :aria-label="t('header.help')" aria-haspopup="true">
              <CircleHelp class="size-5" />
            </button>
          </template>
          <SbDropdownItem @click="open(DOCS_URL)">{{ t('header.docs') }}</SbDropdownItem>
          <SbDropdownItem @click="open(GITHUB_URL)">{{ t('header.github') }}</SbDropdownItem>
          <SbDropdownItem @click="bugReportOpen = true">{{ t('header.bugReport') }}</SbDropdownItem>
          <SbDropdownItem @click="update.checkForUpdates()">{{ t('update.checkNow') }}</SbDropdownItem>
          <p v-if="bridge.version" class="px-3 pt-2 pb-1 text-xs text-text-tertiary">v{{ bridge.version }}</p>
        </SbDropdown>

        <SbDropdown align="right">
          <template #trigger>
            <button type="button" :class="iconButton" :aria-label="t('header.preferences')" aria-haspopup="true">
              <SlidersHorizontal class="size-5" />
            </button>
          </template>
          <p class="px-3 pt-1.5 pb-1 text-xs font-medium text-text-tertiary">{{ t('header.language') }}</p>
          <SbDropdownItem v-for="l in ['en', 'ru']" :key="l" @click="setLocale(l)">
            <span :class="locale === l ? 'font-medium text-primary-text' : ''">{{ l === 'en' ? 'English' : 'Русский' }}</span>
          </SbDropdownItem>
          <p class="px-3 pt-2 pb-1 text-xs font-medium text-text-tertiary">{{ t('header.theme') }}</p>
          <SbDropdownItem v-for="m in ['auto', 'light', 'dark'] as const" :key="m" @click="mode = m">
            <span :class="mode === m ? 'font-medium text-primary-text' : ''">{{ t(`header.themes.${m}`) }}</span>
          </SbDropdownItem>
        </SbDropdown>
      </div>
    </div>
    <BugReportDialog v-model="bugReportOpen" />
  </header>
</template>
