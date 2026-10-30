<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { technicalDetails, setTechnicalDetails } from '@/composables/useTechnicalDetails'
import { useTheme } from '@/composables/useTheme'
import { useBridgeStore } from '@/stores/bridge'
import { useUpdateStore } from '@/stores/update'
import { SbDropdown, SbDropdownItem } from '@/kit'
import BugReportDialog from '@/components/BugReportDialog.vue'
import { shortcutsOpen } from '@/composables/useKeyboardShortcuts'
import { CircleHelp, SlidersHorizontal, ArrowUpCircle, Heart } from 'lucide-vue-next'
import logoUrl from '@/assets/bridge-logo.svg'
import { tServer, type ServerText } from '@/i18n/server'
import { fleetSummary } from '@/utils/fleetSummary'
import { describeStatus } from '@/composables/useStatusState'
import { availableLocales, chooseLocale, languageName } from '@/i18n'

const { t, te, locale } = useI18n()
const router = useRouter()
const { mode, embedded } = useTheme()
const bridge = useBridgeStore()
const update = useUpdateStore()

const GITHUB_URL = 'https://github.com/trudenboy/sendspin-bt-bridge'
const DOCS_URL = 'https://trudenboy.github.io/sendspin-bt-bridge'
const SPONSOR_URL = 'https://trudenboy.github.io/sendspin-bt-bridge/support/'

const bugReportOpen = ref(false)
const bridgeName = computed(() => bridge.bridge?.name || '')

/** The bridge's own one-line verdict (guidance header), as a pill that opens diagnostics. */
const status = computed(() => {
  const header = (bridge.bridge?.guidance as { header_status?: { tone?: string; label?: string } & ServerText } | null)?.header_status
  return header?.label
    ? { tone: header.tone ?? 'neutral', label: tServer('header', header, header.area === 'issue' ? 'title' : 'label', header.label) }
    : null
})
/** The pill opens diagnostics; its tooltip adds the account state, so it never reads as the whole story. */
const statusTitle = computed(() => {
  const account = bridge.maAccount
  const line = account ? describeStatus('ma_account', account, { t: (k, p) => t(k, p ?? {}), te }).full : ''
  return line ? `${t('header.openDiagnostics')} · ${line}` : t('header.openDiagnostics')
})
/* Inside Home Assistant the runtime row folds into this pill: its title carries where the bridge runs. */
const fleet = computed(() => fleetSummary(bridge.devices))
const runtimeFacts = computed(() => {
  const info = bridge.bridge
  if (!info) return ''
  const runtime = (info.runtime || '').replace(/-/g, '_')
  const key = `header.runtime.${runtime}`
  const parts = [
    runtime && runtime !== 'unknown' ? (te(key) ? t(key) : runtime) : '',
    info.hostname,
    info.ip_address,
    info.uptime ? t('header.upFor', { uptime: info.uptime }) : '',
  ].filter(Boolean)
  return parts.join(' · ')
})
const embeddedPill = computed(() => {
  const speakers = fleet.value.total > 0 ? t('header.speakersConnected', { n: fleet.value.connected, total: fleet.value.total }) : ''
  const label = [status.value?.label, speakers].filter(Boolean).join(' · ')
  return { tone: status.value?.tone ?? 'neutral', label, title: [statusTitle.value, runtimeFacts.value].filter(Boolean).join('\n') }
})
const statusClass: Record<string, string> = {
  success: 'tone-success',
  warning: 'tone-warning',
  error: 'tone-error',
  info: 'tone-info',
  neutral: 'tone-neutral',
}

function setLocale(next: string) {
  void chooseLocale(next)
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
    <div class="flex items-center gap-2 px-4 sm:gap-3 sm:px-6" :class="embedded ? 'h-11' : 'h-16'">
      <router-link v-if="!embedded" to="/" class="flex min-w-0 shrink items-center gap-2.5" :aria-label="t('app.title')">
        <img :src="logoUrl" alt="" class="h-7 w-auto shrink-0" width="53" height="28" />
        <span class="min-w-0 truncate text-base font-semibold text-text-primary sm:shrink-0 sm:text-lg">{{ t('app.title') }}</span>
        <span
          v-if="bridgeName"
          class="hidden min-w-0 truncate rounded-full bg-surface-secondary px-2.5 py-0.5 text-xs text-text-secondary sm:inline"
          :title="t('header.bridgeName')"
        >{{ bridgeName }}</span>
      </router-link>

      <div class="ml-auto flex min-w-0 shrink-0 items-center gap-1 sm:gap-2">
        <button
          v-if="embedded && embeddedPill.label"
          type="button"
          data-status-pill
          class="inline-flex min-w-0 max-w-[60vw] items-center gap-1.5 truncate rounded-full px-3 py-1 text-xs font-medium"
          :class="statusClass[embeddedPill.tone] ?? 'tone-neutral'"
          :title="embeddedPill.title"
          @click="router.push('/diagnostics')"
        >
          <span class="size-1.5 shrink-0 rounded-full bg-current" aria-hidden="true" />
          <span class="truncate">{{ embeddedPill.label }}</span>
        </button>
        <button
          v-else-if="status"
          type="button"
          class="hidden max-w-64 items-center gap-1.5 truncate rounded-full px-3 py-1 text-xs font-medium md:inline-flex"
          :class="statusClass[status.tone] ?? 'tone-neutral'"
          :title="statusTitle"
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
          :aria-label="t('update.badge', { version: update.latestVersion })"
          @click="update.openDialog()"
        >
          <ArrowUpCircle class="size-3.5" aria-hidden="true" />
          <span class="hidden sm:inline">{{ t('update.badge', { version: update.latestVersion }) }}</span>
        </button>

        <a
          v-if="!embedded"
          :href="SPONSOR_URL"
          target="_blank"
          rel="noopener noreferrer"
          class="inline-flex h-10 items-center gap-1.5 rounded-full px-2.5 text-sm font-medium text-text-secondary transition-colors hover:bg-surface-secondary hover:text-text-primary"
          :title="t('header.sponsorHint')"
        >
          <Heart class="size-4 fill-current text-[#e5487a]" aria-hidden="true" />
          <span class="hidden md:inline">{{ t('header.sponsor') }}</span>
          <span class="sr-only md:hidden">{{ t('header.sponsor') }}</span>
        </a>

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
          <SbDropdownItem @click="shortcutsOpen = true">{{ t('shortcuts.title') }}</SbDropdownItem>
          <p v-if="bridge.version" class="px-3 pt-2 pb-1 text-xs text-text-tertiary">v{{ bridge.version }}</p>
        </SbDropdown>

        <SbDropdown align="right">
          <template #trigger>
            <button type="button" :class="iconButton" :aria-label="t('header.preferences')" aria-haspopup="true">
              <SlidersHorizontal class="size-5" />
            </button>
          </template>
          <p class="px-3 pt-1.5 pb-1 text-xs font-medium text-text-tertiary">{{ t('header.language') }}</p>
          <SbDropdownItem v-for="l in availableLocales()" :key="l" @click="setLocale(l)">
            <span :class="locale === l ? 'font-medium text-primary-text' : ''">{{ languageName(l) }}</span>
          </SbDropdownItem>
          <p class="px-3 pt-2 pb-1 text-xs font-medium text-text-tertiary">{{ t('header.theme') }}</p>
          <SbDropdownItem v-for="m in ['auto', 'light', 'dark'] as const" :key="m" @click="mode = m">
            <span :class="mode === m ? 'font-medium text-primary-text' : ''">{{ t(`header.themes.${m}`) }}</span>
          </SbDropdownItem>
          <p class="px-3 pt-2 pb-1 text-xs font-medium text-text-tertiary">{{ t('header.display') }}</p>
          <SbDropdownItem data-technical-toggle role="menuitemcheckbox" :aria-checked="technicalDetails" @click="setTechnicalDetails(!technicalDetails)">
            <span :class="technicalDetails ? 'font-medium text-primary-text' : ''">{{ t('settings.showAdvanced') }}</span>
          </SbDropdownItem>
        </SbDropdown>
      </div>
    </div>
    <BugReportDialog v-model="bugReportOpen" />
  </header>
</template>
