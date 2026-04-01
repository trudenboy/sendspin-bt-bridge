<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { useTheme } from '@/composables/useTheme'
import { useBridgeStore } from '@/stores/bridge'
import { useUpdateStore } from '@/stores/update'
import {
  Bug,
  BookOpen,
  Github,
  Sun,
  Moon,
  Monitor,
  Menu,
  Globe,
  ArrowUpCircle,
} from 'lucide-vue-next'
import { computed, onMounted } from 'vue'

const { t, locale } = useI18n()
const { mode, toggleTheme } = useTheme()
const bridge = useBridgeStore()
const update = useUpdateStore()

const emit = defineEmits<{
  'toggle-sidebar': []
}>()

const themeIcon = {
  light: Sun,
  dark: Moon,
  auto: Monitor,
} as const

const GITHUB_URL = 'https://github.com/trudenboy/sendspin-bt-bridge'
const DOCS_URL = 'https://trudenboy.github.io/sendspin-bt-bridge'

const uptimeFormatted = computed(() => {
  const secs = bridge.snapshot?.uptime_seconds
  if (secs == null) return null
  const h = Math.floor(secs / 3600)
  const m = Math.floor((secs % 3600) / 60)
  if (h > 0) return `${h}${t('header.hourShort')} ${m}${t('header.minShort')}`
  const s = Math.floor(secs % 60)
  return `${m}${t('header.minShort')} ${s}${t('header.secShort')}`
})

type HealthColor = 'green' | 'yellow' | 'red' | 'gray'

const healthColor = computed<HealthColor>(() => {
  const devs = bridge.devices
  if (!devs.length) return 'gray'
  const enabled = devs.filter((d) => d.enabled)
  if (!enabled.length) return 'gray'
  const allGood = enabled.every((d) =>
    ['STREAMING', 'READY', 'IDLE', 'STANDBY'].includes(d.player_state ?? ''),
  )
  if (allGood) return 'green'
  const allBad = enabled.every((d) =>
    ['ERROR', 'OFFLINE'].includes(d.player_state ?? ''),
  )
  if (allBad) return 'red'
  return 'yellow'
})

const healthDotClass = computed(() => {
  const cls: Record<HealthColor, string> = {
    green: 'bg-green-500',
    yellow: 'bg-yellow-500 animate-pulse',
    red: 'bg-red-500 animate-pulse',
    gray: 'bg-gray-400',
  }
  return cls[healthColor.value]
})

const healthLabel = computed(() => {
  const key: Record<HealthColor, string> = {
    green: 'header.healthGood',
    yellow: 'header.healthDegraded',
    red: 'header.healthError',
    gray: 'header.healthUnknown',
  }
  return t(key[healthColor.value])
})

function toggleLocale() {
  const next = locale.value === 'en' ? 'ru' : 'en'
  locale.value = next
  localStorage.setItem('sendspin-ui:locale', next)
}

onMounted(() => {
  update.fetchInfo()
})
</script>

<template>
  <header
    class="fixed top-0 right-0 left-0 z-30 border-b border-surface-secondary bg-surface-card shadow-sm"
  >
    <div class="flex h-16 items-center gap-3 px-4 sm:px-6">
      <!-- Left: Logo + Title + Version + Update -->
      <router-link to="/" class="flex shrink-0 items-center gap-2">
        <img
          src="/bridge-logo.svg"
          alt="Sendspin Bridge"
          class="h-8 w-8"
          width="32"
          height="32"
        />
        <span class="hidden text-lg font-semibold text-text-primary sm:inline">
          {{ t('app.title') }}
        </span>
      </router-link>

      <a
        v-if="bridge.version"
        :href="`${GITHUB_URL}/releases/tag/v${bridge.version}`"
        target="_blank"
        rel="noopener"
        class="rounded-full bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary transition-colors hover:bg-primary/20"
        :title="t('header.viewRelease')"
      >
        v{{ bridge.version }}
      </a>

      <!-- Update badge -->
      <button
        v-if="update.updateAvailable"
        class="hidden items-center gap-1.5 rounded-full bg-warning/10 px-2.5 py-0.5 text-xs font-medium text-warning transition-colors hover:bg-warning/20 sm:inline-flex"
        :title="t('update.available')"
        @click="update.openDialog()"
      >
        <span class="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-warning" />
        {{ t('update.badge', { version: update.latestVersion }) }}
      </button>
      <button
        v-else-if="!update.updateAvailable && bridge.version"
        class="hidden items-center gap-1 rounded-full px-2 py-0.5 text-xs text-text-secondary transition-colors hover:bg-surface-secondary sm:inline-flex"
        :title="t('update.checkNow')"
        :disabled="update.checking"
        @click="update.checkForUpdates()"
      >
        <ArrowUpCircle class="h-3.5 w-3.5" :class="{ 'animate-spin': update.checking }" />
      </button>

      <!-- Center: System info (desktop only) -->
      <div class="ml-auto hidden items-center gap-3 text-xs text-text-secondary md:flex">
        <span v-if="uptimeFormatted" :title="t('header.uptime')">
          {{ t('header.uptime') }}: {{ uptimeFormatted }}
        </span>
        <span
          class="flex items-center gap-1.5"
          :title="healthLabel"
        >
          <span class="inline-block h-2 w-2 rounded-full" :class="healthDotClass" />
          <span class="hidden lg:inline">{{ healthLabel }}</span>
        </span>
      </div>

      <!-- Right: Action buttons + toggles -->
      <div :class="['flex items-center gap-1', { 'ml-auto': !uptimeFormatted && !bridge.devices.length }]">
        <!-- Bug report -->
        <router-link
          to="/diagnostics"
          class="hidden rounded-lg p-2 text-text-secondary transition-colors hover:bg-surface-secondary sm:flex"
          :title="t('header.bugReport')"
        >
          <Bug class="h-4 w-4" />
        </router-link>

        <!-- Docs link -->
        <a
          :href="DOCS_URL"
          target="_blank"
          rel="noopener"
          class="hidden rounded-lg p-2 text-text-secondary transition-colors hover:bg-surface-secondary sm:flex"
          :title="t('header.docs')"
        >
          <BookOpen class="h-4 w-4" />
        </a>

        <!-- GitHub link -->
        <a
          :href="GITHUB_URL"
          target="_blank"
          rel="noopener"
          class="hidden rounded-lg p-2 text-text-secondary transition-colors hover:bg-surface-secondary sm:flex"
          :title="t('header.github')"
        >
          <Github class="h-4 w-4" />
        </a>

        <!-- Language toggle -->
        <button
          class="flex items-center gap-1 rounded-lg px-2 py-2 text-sm text-text-secondary transition-colors hover:bg-surface-secondary"
          :title="locale === 'en' ? 'Русский' : 'English'"
          @click="toggleLocale"
        >
          <Globe class="h-4 w-4" />
          <span class="hidden text-xs font-medium uppercase sm:inline">{{ locale }}</span>
        </button>

        <!-- Theme toggle -->
        <button
          class="rounded-lg p-2 text-text-secondary transition-colors hover:bg-surface-secondary"
          :title="`Theme: ${mode}`"
          @click="toggleTheme"
        >
          <component :is="themeIcon[mode]" class="h-5 w-5" />
        </button>

        <!-- Mobile sidebar toggle -->
        <button
          class="rounded-lg p-2 text-text-secondary lg:hidden"
          :aria-label="t('header.toggleMenu')"
          @click="emit('toggle-sidebar')"
        >
          <Menu class="h-5 w-5" />
        </button>
      </div>
    </div>
  </header>
</template>
