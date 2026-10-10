<script setup lang="ts">
import { confirmDialog } from '@/composables/useConfirm'
import { computed, nextTick, onMounted, ref, type Component } from 'vue'
import { useI18n } from 'vue-i18n'
import { onBeforeRouteLeave, useRoute } from 'vue-router'
import { useConfigStore } from '@/stores/config'
import { useBridgeStore } from '@/stores/bridge'
import { useNotificationStore } from '@/stores/notifications'
import { ApiError } from '@/api/client'
import { SbButton, SbSpinner, SbToggle } from '@/kit'
import {
  Archive,
  AudioLines,
  Bluetooth,
  Download,
  Home,
  Lightbulb,
  Music2,
  Search,
  Settings2,
  ShieldCheck,
} from 'lucide-vue-next'
import { SECTIONS, type ConfigDoc, type FieldDef, type SectionComponent, type SectionDef } from '@/settings/layout'
import SettingField from '@/components/settings/SettingField.vue'
import MaConnectionSection from '@/components/settings/MaConnectionSection.vue'
import SendspinTestSection from '@/components/settings/SendspinTestSection.vue'
import LatencyAssistantSection from '@/components/settings/LatencyAssistantSection.vue'
import AdaptersSection from '@/components/settings/AdaptersSection.vue'
import HaStatusSection from '@/components/settings/HaStatusSection.vue'
import AreaMapSection from '@/components/settings/AreaMapSection.vue'
import PasswordSection from '@/components/settings/PasswordSection.vue'
import TokensSection from '@/components/settings/TokensSection.vue'
import UpdatesSection from '@/components/settings/UpdatesSection.vue'
import BackupSection from '@/components/settings/BackupSection.vue'

const { t } = useI18n()
const configStore = useConfigStore()
const bridge = useBridgeStore()
const notifications = useNotificationStore()

const ICONS: Record<string, Component> = {
  settings: Settings2,
  music: Music2,
  audio: AudioLines,
  bluetooth: Bluetooth,
  home: Home,
  shield: ShieldCheck,
  updates: Download,
  guidance: Lightbulb,
  backup: Archive,
}

const PARTS: Record<SectionComponent, Component> = {
  maConnection: MaConnectionSection,
  sendspinTest: SendspinTestSection,
  latencyAssistant: LatencyAssistantSection,
  adapters: AdaptersSection,
  haStatus: HaStatusSection,
  areaMap: AreaMapSection,
  password: PasswordSection,
  tokens: TokensSection,
  updates: UpdatesSection,
  backup: BackupSection,
}

const ADVANCED_KEY = 'sendspin-ui:settings-advanced'
function readAdvanced() {
  try {
    return localStorage.getItem(ADVANCED_KEY) === '1'
  } catch {
    return false
  }
}
const showAdvanced = ref(readAdvanced())
function setAdvanced(v: boolean) {
  showAdvanced.value = v
  try {
    localStorage.setItem(ADVANCED_KEY, v ? '1' : '0')
  } catch {
    /* remembered for this visit only */
  }
}

const doc = computed(() => (configStore.config ?? {}) as ConfigDoc)

/* Search, as in Home Assistant's settings: matches a field's label, help or key. */
const query = ref('')
const q = computed(() => query.value.trim().toLowerCase())

function fieldMatches(f: FieldDef) {
  if (!q.value) return true
  const base = `settings.fields.${f.key}`
  return [t(`${base}.label`), t(`${base}.help`), f.key].some((x) => x.toLowerCase().includes(q.value))
}

function sectionTitleMatches(section: SectionDef) {
  return !!q.value && t(`settings.sections.${section.id}.title`).toLowerCase().includes(q.value)
}

function visibleFields(section: SectionDef) {
  const searching = !!q.value && !sectionTitleMatches(section)
  return section.fields.filter(
    (f) =>
      // A search also reaches advanced options.
      (showAdvanced.value || !f.advanced || (searching && fieldMatches(f))) &&
      (!f.visible || f.visible(doc.value)) &&
      (!searching || fieldMatches(f)),
  )
}

/** While searching, a section shows only if something in it matches. */
function sectionShown(section: SectionDef) {
  return !q.value || sectionTitleMatches(section) || visibleFields(section).length > 0
}

/** Section components (sign-in, adapters, tokens…) show unless a search hides them. */
function partsShown(section: SectionDef) {
  return !q.value || sectionTitleMatches(section)
}

const nothingFound = computed(() => !!q.value && !SECTIONS.some(sectionShown))

const active = ref(SECTIONS[0]!.id)
function jump(id: string) {
  active.value = id
  document.getElementById(`settings-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

const restartNeeded = ref(false)

async function restartNow() {
  restartNeeded.value = false
  await bridge.restart()
}

async function save() {
  try {
    const result = await configStore.saveConfig()
    const reconfig = (result?.reconfig ?? {}) as { restart_required?: unknown[] }
    if (reconfig.restart_required?.length) {
      restartNeeded.value = true
    } else {
      notifications.success(t('settings.saved'))
    }
    for (const w of result?.warnings ?? []) notifications.warning(w.message)
  } catch (e) {
    notifications.error(e instanceof ApiError ? e.message : t('common.error'))
  }
}

onBeforeRouteLeave(async () => {
  if (!configStore.isDirty) return true
  return confirmDialog({
    title: t('settings.unsaved'),
    message: t('settings.leaveConfirm'),
    confirmLabel: t('settings.leave'),
    danger: true,
  })
})

const route = useRoute()

onMounted(async () => {
  await configStore.fetchConfig()
  // Guidance links straight to a section (``/config#settings-bluetooth``).
  const id = route.hash.replace(/^#settings-/, '')
  if (id && SECTIONS.some((s) => s.id === id)) {
    await nextTick()
    jump(id)
  }
})
</script>

<template>
  <div>
    <div class="mb-6 flex flex-wrap items-center justify-between gap-4">
      <h1 class="text-2xl font-semibold tracking-tight text-text-primary">{{ t('settings.title') }}</h1>
      <SbToggle :model-value="showAdvanced" :label="t('settings.showAdvanced')" @update:model-value="setAdvanced" />
    </div>

    <div
      v-if="restartNeeded"
      class="mb-6 flex flex-wrap items-center gap-3 rounded-(--radius-card) border border-warning/40 bg-warning/10 px-4 py-3"
      role="status"
    >
      <span class="flex-1 text-sm text-text-primary">{{ t('settings.restartNeeded') }}</span>
      <SbButton size="sm" variant="warning" @click="restartNow">{{ t('settings.restartNow') }}</SbButton>
    </div>

    <div
      v-if="configStore.loadError && !configStore.loading"
      class="flex flex-col items-center gap-3 rounded-(--radius-card) border border-error/40 bg-error/8 px-6 py-10 text-center"
      role="alert"
    >
      <p class="font-medium text-text-primary">{{ t('settings.loadFailed') }}</p>
      <p class="text-sm text-text-secondary">{{ configStore.loadError }}</p>
      <SbButton variant="outline" size="sm" @click="configStore.fetchConfig()">{{ t('settings.retry') }}</SbButton>
    </div>

    <div v-else-if="configStore.loading || !configStore.config" class="flex items-center justify-center py-20">
      <SbSpinner size="lg" :label="t('common.loading')" />
    </div>

    <div v-else class="lg:grid lg:grid-cols-[13rem_1fr] lg:gap-8">
      <!-- Section navigation: a sticky list on wide screens, a scrolling chip row on phones -->
      <nav :aria-label="t('settings.sectionsNav')" class="mb-4 lg:mb-0">
        <ul class="flex gap-1 overflow-x-auto pb-1 [scrollbar-width:none] lg:sticky lg:top-24 lg:flex-col lg:overflow-visible">
          <li v-for="s in SECTIONS.filter(sectionShown)" :key="s.id" class="shrink-0">
            <button
              type="button"
              class="flex w-full items-center gap-2.5 rounded-(--radius-button) px-3 py-2 text-left text-sm whitespace-nowrap transition-colors"
              :class="
                active === s.id
                  ? 'bg-primary/12 font-medium text-primary-text'
                  : 'text-text-secondary hover:bg-surface-secondary hover:text-text-primary'
              "
              :aria-current="active === s.id ? 'true' : undefined"
              @click="jump(s.id)"
            >
              <component :is="ICONS[s.icon]" class="size-4 shrink-0" aria-hidden="true" />
              {{ t(`settings.sections.${s.id}.title`) }}
            </button>
          </li>
        </ul>
      </nav>

      <div class="min-w-0 space-y-6 pb-24">
        <div class="relative">
          <Search class="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-text-tertiary" aria-hidden="true" />
          <input
            v-model="query"
            type="search"
            :placeholder="t('settings.search')"
            :aria-label="t('settings.search')"
            class="h-10 w-full rounded-(--radius-input) border border-border-strong bg-surface-card pr-3 pl-9 text-sm text-text-primary outline-none placeholder:text-text-disabled focus:border-primary focus:ring-2 focus:ring-primary/25"
          />
        </div>
        <p v-if="nothingFound" class="py-10 text-center text-sm text-text-secondary">{{ t('settings.nothingFound', { q: query }) }}</p>
        <section
          v-for="s in SECTIONS.filter(sectionShown)"
          :id="`settings-${s.id}`"
          :key="s.id"
          class="scroll-mt-24 overflow-hidden rounded-(--radius-card) border border-border bg-surface-card"
          :aria-labelledby="`settings-${s.id}-title`"
        >
          <!-- Section header: larger, with its icon and a rule under it, so it
               never reads as one more setting. -->
          <header class="flex items-start gap-3 border-b border-border bg-surface-secondary/40 px-4 py-4 sm:px-5">
            <span class="mt-0.5 inline-flex size-9 shrink-0 items-center justify-center rounded-full bg-primary/12 text-primary-text">
              <component :is="ICONS[s.icon]" class="size-5" aria-hidden="true" />
            </span>
            <div class="min-w-0">
              <h2 :id="`settings-${s.id}-title`" class="text-lg font-semibold leading-tight text-text-primary">
                {{ t(`settings.sections.${s.id}.title`) }}
              </h2>
              <p class="mt-1 text-sm text-text-secondary">{{ t(`settings.sections.${s.id}.description`) }}</p>
            </div>
          </header>
          <div class="divide-y divide-border px-4 pb-1 sm:px-5">
            <template v-if="partsShown(s)">
              <component :is="PARTS[part]" v-for="part in s.before ?? []" :key="part" />
            </template>
            <SettingField v-for="f in visibleFields(s)" :key="f.key" :field="f" />
            <template v-if="partsShown(s)">
              <component :is="PARTS[part]" v-for="part in s.after ?? []" :key="part" />
            </template>
          </div>
        </section>
      </div>
    </div>

    <!-- Floating save bar, shown only while there is something to save -->
    <Transition
      enter-active-class="transition duration-200 ease-out"
      enter-from-class="translate-y-4 opacity-0"
      leave-active-class="transition duration-150 ease-in"
      leave-to-class="translate-y-4 opacity-0"
    >
      <div
        v-if="configStore.isDirty"
        class="fixed inset-x-4 bottom-20 z-40 mx-auto flex max-w-xl items-center gap-3 rounded-(--radius-card) border border-border bg-surface-raised px-4 py-3 shadow-lg lg:bottom-6"
        role="region"
        :aria-label="t('settings.unsaved')"
      >
        <span class="flex-1 text-sm text-text-primary">{{ t('settings.unsaved') }}</span>
        <SbButton variant="ghost" size="sm" @click="configStore.resetChanges()">{{ t('settings.discard') }}</SbButton>
        <SbButton size="sm" :loading="configStore.saving" @click="save">{{ t('settings.save') }}</SbButton>
      </div>
    </Transition>
  </div>
</template>
