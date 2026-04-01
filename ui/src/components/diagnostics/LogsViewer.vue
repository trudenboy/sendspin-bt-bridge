<script setup lang="ts">
import { ref, computed, nextTick, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { SbButton, SbBadge, SbSpinner, SbEmptyState } from '@/kit'
import { RefreshCw, Download, Search, ArrowDownToLine } from 'lucide-vue-next'
import { getLogs, downloadLogs, setLogLevel } from '@/api/diagnostics'

type LogLevel = 'ERROR' | 'WARNING' | 'INFO' | 'DEBUG'

interface ParsedLine {
  raw: string
  timestamp: string
  level: LogLevel
  message: string
}

const { t } = useI18n()

const loading = ref(false)
const lines = ref<ParsedLine[]>([])
const searchQuery = ref('')
const autoScroll = ref(true)
const logContainer = ref<HTMLElement | null>(null)

const visibleLevels = ref<Set<LogLevel>>(new Set(['ERROR', 'WARNING', 'INFO', 'DEBUG']))
const selectedLevel = ref('INFO')
const applyingLevel = ref(false)

const levelOrder: LogLevel[] = ['ERROR', 'WARNING', 'INFO', 'DEBUG']

const levelColors: Record<LogLevel, string> = {
  ERROR: 'text-red-500',
  WARNING: 'text-yellow-500',
  INFO: 'text-blue-400',
  DEBUG: 'text-gray-400',
}

const levelBadgeTone: Record<LogLevel, 'error' | 'warning' | 'info' | 'neutral'> = {
  ERROR: 'error',
  WARNING: 'warning',
  INFO: 'info',
  DEBUG: 'neutral',
}

function parseLevel(raw: string): LogLevel {
  const upper = raw.toUpperCase()
  if (upper.includes('ERROR') || upper.includes('CRITICAL') || upper.includes('FATAL'))
    return 'ERROR'
  if (upper.includes('WARNING') || upper.includes('WARN')) return 'WARNING'
  if (upper.includes('DEBUG')) return 'DEBUG'
  return 'INFO'
}

function parseLine(raw: string): ParsedLine {
  // Common patterns: "2025-01-01 12:00:00 INFO message" or ISO timestamps
  const tsMatch = raw.match(/^(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}[^\s]*)/)
  const timestamp = tsMatch ? tsMatch[1] : ''
  const rest = timestamp ? raw.slice(timestamp.length).trim() : raw
  const level = parseLevel(rest)
  const message = rest
    .replace(/^\s*-?\s*(ERROR|WARNING|WARN|INFO|DEBUG|CRITICAL|FATAL)\s*-?\s*/i, '')
    .trim()

  return { raw, timestamp, level, message: message || rest }
}

const filteredLines = computed(() => {
  let result = lines.value.filter((l) => visibleLevels.value.has(l.level))
  if (searchQuery.value) {
    const q = searchQuery.value.toLowerCase()
    result = result.filter((l) => l.raw.toLowerCase().includes(q))
  }
  return result
})

function toggleLevel(level: LogLevel) {
  const next = new Set(visibleLevels.value)
  if (next.has(level)) {
    if (next.size > 1) next.delete(level)
  } else {
    next.add(level)
  }
  visibleLevels.value = next
}

async function fetchLogs() {
  loading.value = true
  try {
    const data = await getLogs(200)
    lines.value = (data.logs ?? []).map(parseLine)
    if (autoScroll.value) {
      await nextTick()
      scrollToBottom()
    }
  } catch {
    lines.value = []
  } finally {
    loading.value = false
  }
}

function scrollToBottom() {
  if (logContainer.value) {
    logContainer.value.scrollTop = logContainer.value.scrollHeight
  }
}

async function applyLogLevel() {
  applyingLevel.value = true
  try {
    await setLogLevel(selectedLevel.value)
  } finally {
    applyingLevel.value = false
  }
}

onMounted(fetchLogs)
</script>

<template>
  <div class="space-y-4">
    <!-- Header -->
    <div class="flex flex-wrap items-center justify-between gap-3">
      <h2 class="text-lg font-semibold text-text-primary">
        {{ t('diagnostics.logs.title') }}
      </h2>
      <div class="flex flex-wrap items-center gap-2">
        <!-- Backend log level -->
        <label class="text-sm text-text-secondary">{{ t('diagnostics.logs.logLevel') }}</label>
        <select
          v-model="selectedLevel"
          class="h-8 rounded-md border border-border bg-surface-primary px-2 text-sm text-text-primary"
        >
          <option v-for="lvl in levelOrder" :key="lvl" :value="lvl">{{ lvl }}</option>
        </select>
        <SbButton variant="secondary" size="sm" :loading="applyingLevel" @click="applyLogLevel">
          {{ t('diagnostics.logs.apply') }}
        </SbButton>
      </div>
    </div>

    <!-- Toolbar -->
    <div class="flex flex-wrap items-center gap-2">
      <!-- Level filters -->
      <template v-for="lvl in levelOrder" :key="lvl">
        <button
          class="rounded-full border px-2.5 py-0.5 text-xs font-medium transition-colors"
          :class="
            visibleLevels.has(lvl)
              ? 'border-transparent bg-surface-secondary text-text-primary'
              : 'border-border bg-transparent text-text-tertiary opacity-50'
          "
          @click="toggleLevel(lvl)"
        >
          <SbBadge :tone="levelBadgeTone[lvl]" size="sm" dot>{{ lvl }}</SbBadge>
        </button>
      </template>

      <span class="mx-1" />

      <!-- Search -->
      <div class="relative flex-1" style="min-width: 160px; max-width: 280px">
        <Search class="absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-text-tertiary" />
        <input
          v-model="searchQuery"
          type="text"
          :placeholder="t('diagnostics.logs.search')"
          class="h-8 w-full rounded-md border border-border bg-surface-primary pl-8 pr-3 text-sm text-text-primary placeholder:text-text-tertiary focus:outline-none focus:ring-1 focus:ring-primary"
        />
      </div>

      <span class="flex-1" />

      <!-- Auto-scroll -->
      <button
        class="flex items-center gap-1.5 rounded-md px-2 py-1 text-xs transition-colors"
        :class="
          autoScroll
            ? 'bg-primary/10 text-primary'
            : 'text-text-secondary hover:text-text-primary'
        "
        @click="autoScroll = !autoScroll"
      >
        <ArrowDownToLine class="h-3.5 w-3.5" />
        {{ t('diagnostics.logs.autoScroll') }}
      </button>

      <!-- Refresh -->
      <SbButton variant="secondary" size="sm" :loading="loading" @click="fetchLogs">
        <template #icon-left><RefreshCw class="h-4 w-4" /></template>
        {{ t('diagnostics.logs.refresh') }}
      </SbButton>

      <!-- Download -->
      <SbButton variant="secondary" size="sm" @click="downloadLogs()">
        <template #icon-left><Download class="h-4 w-4" /></template>
        {{ t('diagnostics.logs.download') }}
      </SbButton>
    </div>

    <!-- Line count -->
    <p class="text-xs text-text-tertiary">
      {{
        t('diagnostics.logs.showingLines', {
          shown: filteredLines.length,
          total: lines.length,
        })
      }}
    </p>

    <!-- Loading -->
    <div v-if="loading && lines.length === 0" class="flex justify-center py-12">
      <SbSpinner size="lg" />
    </div>

    <!-- Empty -->
    <SbEmptyState
      v-else-if="filteredLines.length === 0"
      :title="t('diagnostics.logs.noLogs')"
    />

    <!-- Log lines -->
    <div
      v-else
      ref="logContainer"
      class="max-h-[600px] overflow-y-auto rounded-lg border border-border bg-surface-secondary"
    >
      <div class="divide-y divide-border/40">
        <div
          v-for="(line, idx) in filteredLines"
          :key="idx"
          class="flex gap-2 px-3 py-1 font-mono text-sm"
          :class="idx % 2 === 0 ? 'bg-surface-secondary' : 'bg-surface-primary/40'"
        >
          <!-- Timestamp -->
          <span
            v-if="line.timestamp"
            class="shrink-0 whitespace-nowrap text-text-tertiary"
          >
            {{ line.timestamp }}
          </span>

          <!-- Level badge -->
          <span class="shrink-0">
            <SbBadge :tone="levelBadgeTone[line.level]" size="sm">{{ line.level }}</SbBadge>
          </span>

          <!-- Message -->
          <span :class="levelColors[line.level]" class="min-w-0 break-all">
            {{ line.message }}
          </span>
        </div>
      </div>
    </div>
  </div>
</template>
