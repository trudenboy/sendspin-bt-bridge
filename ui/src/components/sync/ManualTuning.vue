<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { RotateCcw } from 'lucide-vue-next'
import { ENDED, errorCode, useSyncStore } from '@/stores/sync'
import { SbBadge, SbButton, SbCard } from '@/kit'
import DelayRow from './DelayRow.vue'
import ListeningBar from './ListeningBar.vue'

const emit = defineEmits<{
  /** Whether any delay differs from where this session started (the view offers a profile on leaving). */
  changed: [boolean]
}>()

const { t } = useI18n()
const sync = useSyncStore()

/** A/B: each member plays for two clicks (P = 2 s) before the other takes over. */
const AB_MS = 4000

const session = computed(() => sync.session)
const departed = computed(() => new Set(session.value?.departed ?? []))
const members = computed(() => session.value?.members ?? [])
const present = computed(() => members.value.filter((m) => !departed.value.has(m.ma_player_id)))
const nameOf = (id: string) => members.value.find((m) => m.ma_player_id === id)?.name ?? id

/* -- delays ------------------------------------------------------------------------ */

/** What is on screen (and written), and where the session started; seeded once per member. */
const values = reactive<Record<string, number>>({})
const initial: Record<string, number> = {}

watch(
  members,
  (list) => {
    for (const m of list) {
      if (!m.adjustable) continue
      const id = m.ma_player_id
      if (!(id in initial)) {
        initial[id] = m.delay_ms ?? 0
        values[id] = m.delay_ms ?? 0
      } else if (m.delay_ms != null && !sync.hasPendingWrite(id)) {
        // Another tab, or the bridge: follow it unless this screen has an edit on the way.
        values[id] = m.delay_ms
      }
    }
  },
  { immediate: true },
)

/** A live write Music Assistant refused: say so, and show what it really holds. */
const writeError = ref('')
watch(
  () => sync.writeFailure,
  (failure) => {
    if (!failure || !(failure.member in initial)) return
    const name = nameOf(failure.member)
    writeError.value =
      failure.code === 'ma_forbidden'
        ? t('sync.manual.writeForbidden', { name })
        : t('sync.manual.writeFailed', { name, code: failure.code })
    if (sync.hasPendingWrite(failure.member)) return
    const held = sync.group?.members.find((m) => m.ma_player_id === failure.member)?.delay_ms
    values[failure.member] = held ?? initial[failure.member]!
  },
)

const changedIds = computed(() => Object.keys(initial).filter((id) => values[id] !== initial[id]))
watch(
  () => changedIds.value.length > 0,
  (changed) => emit('changed', changed),
  { immediate: true },
)

const note = ref('')

function write(member: string, ms: number) {
  note.value = ''
  writeError.value = ''
  values[member] = ms
  sync.setDelay(member, ms)
}

/** Back to the session start: only the members that moved are written. */
function reset() {
  for (const id of changedIds.value) {
    if (departed.value.has(id)) continue
    write(id, initial[id]!)
  }
  note.value = t('sync.manual.resetDone')
}

/* -- listening --------------------------------------------------------------------- */

const failure = ref('')
const abPair = ref<readonly [string, string] | null>(null)
let abTimer: ReturnType<typeof setInterval> | null = null

function stopAb() {
  if (abTimer) clearInterval(abTimer)
  abTimer = null
  abPair.value = null
}

async function run(call: Promise<unknown>) {
  try {
    await call
    failure.value = ''
    return true
  } catch (e) {
    failure.value = t('sync.listen.failed', { code: errorCode(e) })
    return false
  }
}

/**
 * The click track's state as this screen asked for it, until the session says otherwise:
 * solo and A/B are offered only while the clicks play, and must be there right after Play.
 */
const signalWanted = ref<boolean | null>(null)
const signalRunning = computed(() => signalWanted.value ?? !!session.value?.signal_running)
watch(
  () => session.value?.signal_running,
  () => {
    signalWanted.value = null
  },
)

function onSignal(action: 'start' | 'stop') {
  if (action === 'stop') stopAb()
  signalWanted.value = action === 'start'
  void run(sync.signal(action)).then((ok) => {
    if (!ok) signalWanted.value = null
  })
}

function onSolo(member: string | null) {
  stopAb()
  void run(sync.solo(member))
}

function onAb(a: string, b: string) {
  stopAb()
  const pair = [a, b] as const
  abPair.value = pair
  let current: string = a
  abTimer = setInterval(() => {
    current = current === a ? b : a
    void run(sync.solo(current)).then((ok) => {
      if (!ok && abPair.value === pair) stopAb()
    })
  }, AB_MS)
  void run(sync.ab(a, b)).then((ok) => {
    if (!ok && abPair.value === pair) stopAb()
  })
}

// Nothing to alternate once the session is over, or a member of the pair left.
watch(
  () => [session.value?.state, [...departed.value]] as const,
  ([state, gone]) => {
    const pair = abPair.value
    if (!pair) return
    if (!state || ENDED.has(state) || gone.includes(pair[0]) || gone.includes(pair[1])) stopAb()
  },
)

onBeforeUnmount(stopAb)

const listenStatus = computed(() => {
  const s = session.value
  if (abPair.value) return t('sync.listen.alternating', { a: nameOf(abPair.value[0]), b: nameOf(abPair.value[1]) })
  if (!signalRunning.value) return t('sync.listen.idle')
  if (s?.soloed) return t('sync.listen.soloed', { name: nameOf(s.soloed) })
  return t('sync.listen.playing')
})
</script>

<template>
  <div class="mt-4 flex flex-col gap-4">
    <SbCard>
      <h2 class="mb-3 text-sm font-medium text-text-secondary">{{ t('sync.listen.title') }}</h2>
      <ListeningBar
        :members="present"
        :signal-running="signalRunning"
        :soloed="session?.soloed ?? null"
        :alternating="abPair"
        @signal="onSignal"
        @solo="onSolo"
        @ab="onAb"
      />
      <p data-listen-status role="status" aria-live="polite" class="mt-3 text-sm text-text-secondary">
        {{ listenStatus }}<template v-if="note"> {{ note }}</template>
      </p>
      <p v-if="failure" data-listen-error role="alert" class="mt-2 text-sm text-error">{{ failure }}</p>
    </SbCard>

    <SbCard>
      <div class="mb-1 flex flex-wrap items-center justify-between gap-2">
        <h2 class="text-sm font-medium text-text-secondary">{{ t('sync.manual.title') }}</h2>
        <SbButton type="button" size="sm" variant="outline" data-reset :disabled="changedIds.length === 0" @click="reset">
          <template #icon-left><RotateCcw aria-hidden="true" /></template>
          {{ t('sync.manual.reset') }}
        </SbButton>
      </div>
      <p class="mb-2 text-xs text-text-secondary">{{ t('sync.manual.help') }}</p>
      <p v-if="writeError" data-write-error role="alert" class="mb-2 text-sm text-error">{{ writeError }}</p>
      <ul class="divide-y divide-border">
        <li v-for="m in members" :key="m.ma_player_id">
          <div
            v-if="departed.has(m.ma_player_id)"
            :data-departed="m.ma_player_id"
            class="flex items-center justify-between gap-3 py-3 text-text-secondary"
          >
            <span class="min-w-0 flex-1 truncate line-through">{{ m.name }}</span>
            <SbBadge tone="warning" size="sm">{{ t('sync.manual.departed') }}</SbBadge>
          </div>
          <DelayRow
            v-else-if="m.adjustable"
            :member="m"
            :model-value="values[m.ma_player_id] ?? 0"
            @update:model-value="write(m.ma_player_id, $event)"
          />
          <div v-else data-fixed-row class="flex items-center justify-between gap-3 py-3">
            <span class="min-w-0 flex-1 truncate text-text-primary">{{ m.name }}</span>
            <SbBadge tone="warning" size="sm">{{ t('sync.origin.other') }}</SbBadge>
          </div>
        </li>
      </ul>
    </SbCard>
  </div>
</template>
