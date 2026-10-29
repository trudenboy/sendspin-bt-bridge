<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { Pencil, Trash2 } from 'lucide-vue-next'
import { useSyncStore } from '@/stores/sync'
import { confirmDialog } from '@/composables/useConfirm'
import { ApiError } from '@/api/client'
import { SbBadge, SbButton, SbDialog } from '@/kit'
import ProfileDiffDialog from './ProfileDiffDialog.vue'
import type { SyncProfile } from '@/api/types'

const props = defineProps<{
  groupId: string
  /** Another client tunes the group: writing delays is refused until it ends. */
  readOnly?: boolean
}>()

const { t } = useI18n()
const sync = useSyncStore()

const members = computed(() => (sync.group?.id === props.groupId ? sync.group.members : []))
const profiles = computed(() => sync.profiles.filter((p) => p.group_id === props.groupId))

/** Last outcome, announced politely; ``skipped`` lists who an apply left out. */
const status = ref('')
const skipped = ref<string[]>([])
const failure = ref('')
const busy = ref(false)

function codeOf(e: unknown) {
  return e instanceof ApiError ? e.code : 'error'
}

function report(message: string, left: string[] = []) {
  failure.value = ''
  status.value = message
  skipped.value = left
}

function fail(e: unknown) {
  status.value = ''
  skipped.value = []
  failure.value = t('sync.profiles.failed', { code: codeOf(e) })
}

/** A member's name, or its player ID when it is no longer in the group. */
function memberName(id: string) {
  return members.value.find((m) => m.ma_player_id === id)?.name ?? t('sync.profiles.playerId', { id })
}

/** One look for the native inputs (``SbInput`` puts extra attributes on its wrapper, not the input). */
const inputClass =
  'rounded-(--radius-input) border border-border-strong bg-surface-card px-3 text-sm text-text-primary'

const root = ref<HTMLElement | null>(null)

/** Focus an element of the panel once the list has re-rendered. */
async function focusIn(selector: string) {
  await nextTick()
  root.value?.querySelector<HTMLElement>(selector)?.focus()
}

watch(
  () => props.groupId,
  (id) => {
    if (!id) return
    sync.fetchProfiles(id).catch(fail)
  },
  { immediate: true },
)

/* -- apply, with the diff first ---------------------------------------------------- */

const diffOpen = ref(false)
const diffProfile = ref<SyncProfile | null>(null)

function askApply(profile: SyncProfile) {
  diffProfile.value = profile
  diffOpen.value = true
}

async function apply() {
  const profile = diffProfile.value
  if (!profile) return
  busy.value = true
  try {
    const result = await sync.applyProfile(profile.id)
    diffOpen.value = false
    report(t('sync.profiles.applied', { name: profile.name }), result.skipped.map(memberName))
  } catch (e) {
    diffOpen.value = false
    fail(e)
  } finally {
    busy.value = false
  }
}

/* -- save current ------------------------------------------------------------------ */

const saveOpen = ref(false)
const newName = ref('')
const newNotes = ref('')
const nameMissing = ref(false)

function askSave() {
  newName.value = ''
  newNotes.value = ''
  nameMissing.value = false
  saveOpen.value = true
}

watch(newName, (v) => {
  if (v.trim()) nameMissing.value = false
})

async function save() {
  const name = newName.value.trim()
  if (busy.value) return
  if (!name) {
    // Not a disabled button: the name may arrive in the same tick as the click.
    nameMissing.value = true
    await nextTick()
    document.getElementById('sync-profile-name')?.focus()
    return
  }
  busy.value = true
  try {
    await sync.saveProfile(name, 'device', newNotes.value.trim())
    saveOpen.value = false
    report(t('sync.profiles.saved', { name }))
  } catch (e) {
    saveOpen.value = false
    fail(e)
  } finally {
    busy.value = false
  }
}

/* -- rename inline ----------------------------------------------------------------- */

const renaming = ref<string | null>(null)
const renameValue = ref('')
const renameInput = ref<HTMLInputElement[] | HTMLInputElement | null>(null)

async function startRename(profile: SyncProfile) {
  renaming.value = profile.id
  renameValue.value = profile.name
  await nextTick()
  const el = Array.isArray(renameInput.value) ? renameInput.value[0] : renameInput.value
  el?.focus()
  el?.select()
}

function cancelRename() {
  const id = renaming.value
  renaming.value = null
  if (id) void focusIn(`[data-rename="${id}"]`)
}

async function commitRename(profile: SyncProfile) {
  const name = renameValue.value.trim()
  if (!name || name === profile.name) {
    cancelRename()
    return
  }
  try {
    await sync.renameProfile(profile.id, name)
    report(t('sync.profiles.renamed', { name }))
  } catch (e) {
    fail(e)
  } finally {
    renaming.value = null
    void focusIn(`[data-rename="${profile.id}"]`)
  }
}

/* -- delete ------------------------------------------------------------------------ */

async function remove(profile: SyncProfile) {
  const ok = await confirmDialog({
    title: t('sync.profiles.deleteTitle', { name: profile.name }),
    message: t('sync.profiles.deleteMessage'),
    confirmLabel: t('sync.profiles.delete'),
    cancelLabel: t('sync.profiles.cancel'),
    danger: true,
  })
  if (!ok) return
  const list = profiles.value
  const at = list.findIndex((p) => p.id === profile.id)
  const next = list[at + 1] ?? list[at - 1]
  try {
    await sync.removeProfile(profile.id)
    report(t('sync.profiles.deleted', { name: profile.name }))
    // Keep the keyboard in the list: the next profile, else the previous one, else the heading.
    void focusIn(next ? `[data-apply="${next.id}"]` : '#sync-profiles-title')
  } catch (e) {
    fail(e)
  }
}
</script>

<template>
  <section ref="root" aria-labelledby="sync-profiles-title">
    <div class="mb-2 flex flex-wrap items-center justify-between gap-2">
      <h2 id="sync-profiles-title" tabindex="-1" class="text-sm font-medium text-text-secondary outline-none">{{ t('sync.profiles.title') }}</h2>
      <SbButton type="button" variant="outline" size="sm" data-save-current @click="askSave">
        {{ t('sync.profiles.saveCurrent') }}
      </SbButton>
    </div>

    <p v-if="profiles.length === 0" class="text-sm text-text-secondary">{{ t('sync.profiles.empty') }}</p>
    <ul v-else class="divide-y divide-border">
      <li v-for="p in profiles" :key="p.id" :data-profile="p.id" class="flex flex-wrap items-center gap-2 py-2">
        <template v-if="renaming === p.id">
          <label :for="`rename-${p.id}`" class="sr-only">{{ t('sync.profiles.renameLabel', { name: p.name }) }}</label>
          <input
            :id="`rename-${p.id}`"
            ref="renameInput"
            v-model="renameValue"
            :data-rename-input="p.id"
            type="text"
            :class="[inputClass, 'min-w-0 flex-1 py-1.5']"
            @keydown.enter.prevent="commitRename(p)"
            @keydown.escape.prevent.stop="cancelRename"
          />
          <SbButton type="button" size="sm" @click="commitRename(p)">{{ t('sync.profiles.save') }}</SbButton>
          <SbButton type="button" size="sm" variant="ghost" @click="cancelRename">{{ t('sync.profiles.cancel') }}</SbButton>
        </template>
        <template v-else>
          <span class="min-w-0 flex-1 truncate text-text-primary">
            {{ p.name }}
            <span v-if="p.notes" class="block truncate text-xs text-text-secondary">{{ p.notes }}</span>
          </span>
          <SbBadge size="sm" tone="neutral">{{ t(`sync.profiles.kind.${p.kind}`) }}</SbBadge>
          <SbButton type="button" size="sm" :data-apply="p.id" :disabled="readOnly" @click="askApply(p)">{{ t('sync.profiles.apply') }}</SbButton>
          <SbButton
            type="button"
            size="sm"
            variant="ghost"
            icon
            :data-rename="p.id"
            :aria-label="`${t('sync.profiles.rename')}: ${p.name}`"
            :title="t('sync.profiles.rename')"
            @click="startRename(p)"
          >
            <Pencil aria-hidden="true" />
          </SbButton>
          <SbButton
            type="button"
            size="sm"
            variant="ghost"
            icon
            :data-delete="p.id"
            :aria-label="`${t('sync.profiles.delete')}: ${p.name}`"
            :title="t('sync.profiles.delete')"
            @click="remove(p)"
          >
            <Trash2 aria-hidden="true" />
          </SbButton>
        </template>
      </li>
    </ul>

    <div role="status" aria-live="polite" class="mt-2 text-sm">
      <p v-if="status" class="text-text-primary">{{ status }}</p>
      <p v-if="skipped.length" data-skipped class="text-text-secondary">
        {{ t('sync.profiles.skipped', { n: skipped.length }, skipped.length) }}: {{ skipped.join(', ') }}
      </p>
    </div>
    <p v-if="failure" role="alert" class="mt-2 text-sm text-error">{{ failure }}</p>

    <ProfileDiffDialog v-model="diffOpen" :profile="diffProfile" :members="members" :busy="busy" @apply="apply" />

    <SbDialog v-model="saveOpen" :title="t('sync.profiles.saveCurrent')">
      <div class="flex flex-col gap-3">
        <div class="flex flex-col gap-1">
          <label for="sync-profile-name" class="text-sm font-medium text-text-primary">{{ t('sync.profiles.name') }}</label>
          <input
            id="sync-profile-name"
            v-model="newName"
            data-profile-name
            type="text"
            required
            maxlength="100"
            :aria-invalid="nameMissing || undefined"
            :aria-describedby="nameMissing ? 'sync-profile-name-error' : undefined"
            :class="[inputClass, 'py-2', nameMissing && 'border-error']"
            @keydown.enter.prevent="save"
          />
          <p v-if="nameMissing" id="sync-profile-name-error" data-profile-name-error role="alert" class="text-xs text-error">
            {{ t('sync.profiles.nameRequired') }}
          </p>
        </div>
        <div class="flex flex-col gap-1">
          <label for="sync-profile-notes" class="text-sm font-medium text-text-primary">{{ t('sync.profiles.notes') }}</label>
          <input
            id="sync-profile-notes"
            v-model="newNotes"
            data-profile-notes
            type="text"
            :class="[inputClass, 'py-2']"
            @keydown.enter.prevent="save"
          />
        </div>
        <div class="flex justify-end gap-3">
          <SbButton type="button" variant="outline" @click="saveOpen = false">{{ t('sync.profiles.cancel') }}</SbButton>
          <SbButton type="button" data-profile-save :loading="busy" @click="save">
            {{ t('sync.profiles.save') }}
          </SbButton>
        </div>
      </div>
    </SbDialog>
  </section>
</template>
