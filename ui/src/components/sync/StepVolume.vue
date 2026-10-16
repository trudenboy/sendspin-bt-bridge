<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { SbBadge, SbButton } from '@/kit'
import VolumeSlider from '@/components/playback/VolumeSlider.vue'
import type { SyncMember } from '@/api/types'
import { speakerName } from '@/sync/names'
import LevelMeter from './LevelMeter.vue'

/**
 * Preparation, step 3: every member's volume (Music Assistant's), so the clicks are clearly audible
 * where the operator measures; with the microphone open, its level meter too.
 */
defineProps<{
  members: SyncMember[]
  /** Member id -> volume as the bridge last read or set it; null: the player has no volume. */
  volumes: Record<string, number | null | undefined>
  /** The microphone level now, RMS 0..1 (the measurement wizard; none in manual tuning). */
  read?: () => number
  disabled?: boolean
  /** Why the last change failed, already translated. */
  error: string
  /** ``skip`` where Cancel only skips the preparation (the Manual tab) instead of ending the session. */
  cancelLabel?: 'cancel' | 'skip'
}>()
const emit = defineEmits<{ volume: [member: string, level: number]; next: []; back: []; cancel: [] }>()

const { t } = useI18n()
const nameOf = (m: SyncMember) => speakerName(m.name, m.ma_player_id)
</script>

<template>
  <div class="flex flex-col gap-4">
    <p class="text-sm text-text-primary">{{ t('sync.prep.volume.help') }}</p>
    <ul class="flex flex-col gap-2">
      <li v-for="m in members" :key="m.ma_player_id" :data-volume-row="m.ma_player_id" class="flex flex-col gap-1">
        <span class="flex flex-wrap items-center gap-2">
          <span :title="m.name" class="min-w-0 truncate text-sm font-medium text-text-primary">{{ nameOf(m) }}</span>
          <SbBadge v-if="volumes[m.ma_player_id] === 0" :data-silent="m.ma_player_id" tone="warning" size="sm">
            {{ t('sync.prep.volume.silent') }}
          </SbBadge>
        </span>
        <VolumeSlider
          v-if="volumes[m.ma_player_id] != null"
          :mac="m.ma_player_id"
          :name="nameOf(m)"
          :volume="volumes[m.ma_player_id]!"
          :muted="false"
          :mutable="false"
          :disabled="disabled"
          @update:volume="emit('volume', m.ma_player_id, $event)"
        />
        <span v-else class="text-xs text-text-secondary">{{ t('sync.prep.volume.noVolume') }}</span>
      </li>
    </ul>
    <template v-if="read">
      <LevelMeter :read="read" />
      <p class="text-xs text-text-secondary">{{ t('sync.wizard.level.help') }}</p>
    </template>
    <p v-if="error" role="alert" class="text-sm text-error">{{ error }}</p>
    <div class="flex justify-end gap-3">
      <SbButton type="button" variant="outline" data-cancel @click="emit('cancel')">
        {{ cancelLabel === 'skip' ? t('sync.prep.skip') : t('sync.wizard.cancel') }}
      </SbButton>
      <SbButton type="button" variant="outline" data-back @click="emit('back')">{{ t('sync.wizard.back') }}</SbButton>
      <SbButton type="button" data-next :disabled="disabled" @click="emit('next')">{{ t('sync.wizard.next') }}</SbButton>
    </div>
  </div>
</template>
