<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useConfigStore } from '@/stores/config'
import { SbInput, SbDropdown, SbDropdownItem, SbCard } from '@/kit'

const { t } = useI18n()
const configStore = useConfigStore()

const bridgeName = computed({
  get: () => configStore.config?.BRIDGE_NAME ?? '',
  set: (v: string) => configStore.updateField('BRIDGE_NAME', v),
})

const timezone = computed({
  get: () => configStore.config?.TZ ?? '',
  set: (v: string) => configStore.updateField('TZ', v),
})

const webPort = computed({
  get: () => String(configStore.config?.WEB_PORT ?? 8080),
  set: (v: string) => configStore.updateField('WEB_PORT', Number(v)),
})

const logLevel = computed({
  get: () => configStore.config?.LOG_LEVEL ?? 'INFO',
  set: (_v: string) => { /* set via selectLogLevel */ },
})

const logLevels = ['DEBUG', 'INFO', 'WARNING', 'ERROR'] as const

function selectLogLevel(level: string) {
  configStore.updateField('LOG_LEVEL', level)
}

const commonTimezones = [
  'UTC',
  'Europe/London',
  'Europe/Berlin',
  'Europe/Moscow',
  'America/New_York',
  'America/Chicago',
  'America/Denver',
  'America/Los_Angeles',
  'Asia/Tokyo',
  'Asia/Shanghai',
  'Asia/Kolkata',
  'Australia/Sydney',
  'Australia/Melbourne',
  'Pacific/Auckland',
]
</script>

<template>
  <div class="space-y-6 py-6">
    <SbCard>
      <template #header>
        <h3 class="text-lg font-semibold text-text-primary">{{ t('config.general') }}</h3>
      </template>
      <div class="space-y-5">
        <SbInput
          v-model="bridgeName"
          :label="t('config.bridgeName')"
          :hint="t('config.bridgeNameHint')"
          placeholder="sendspin-bridge"
        />

        <div>
          <label class="mb-1 block text-sm font-medium text-text-primary">
            {{ t('config.timezone') }}
          </label>
          <SbInput v-model="timezone" placeholder="UTC" />
          <div class="mt-1 flex flex-wrap gap-1">
            <button
              v-for="tz in commonTimezones"
              :key="tz"
              type="button"
              class="rounded-md px-2 py-0.5 text-xs transition-colors"
              :class="timezone === tz
                ? 'bg-primary/15 text-primary font-medium'
                : 'bg-surface-secondary text-text-secondary hover:bg-surface-secondary/80'"
              @click="timezone = tz"
            >
              {{ tz }}
            </button>
          </div>
        </div>

        <SbInput
          v-model="webPort"
          :label="t('config.webPort')"
          type="number"
          placeholder="8080"
        />

        <div>
          <label class="mb-1 block text-sm font-medium text-text-primary">
            {{ t('config.logLevel') }}
          </label>
          <SbDropdown width="full">
            <template #trigger>
              <button
                type="button"
                class="flex w-full items-center justify-between rounded-lg border border-gray-300 bg-surface-primary px-3 py-2 text-sm text-text-primary dark:border-gray-600"
              >
                <span>{{ logLevel }}</span>
                <svg class="h-4 w-4 text-text-secondary" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7" />
                </svg>
              </button>
            </template>
            <SbDropdownItem
              v-for="level in logLevels"
              :key="level"
              @click="selectLogLevel(level)"
            >
              {{ level }}
            </SbDropdownItem>
          </SbDropdown>
        </div>
      </div>
    </SbCard>
  </div>
</template>
