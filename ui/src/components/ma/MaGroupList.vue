<script setup lang="ts">
import { openExternal } from '@/utils/safeUrl'
import { onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { useMaStore } from '@/stores/ma'
import { useBridgeStore } from '@/stores/bridge'
import { SbCard, SbBadge, SbButton, SbEmptyState } from '@/kit'
import { Users, ExternalLink } from 'lucide-vue-next'
import MaNowPlaying from './MaNowPlaying.vue'

const { t } = useI18n()
const ma = useMaStore()
const bridge = useBridgeStore()

/** Bridge players are named "Speaker @ bridge"; the group already says where it is. */
function memberName(name: string | null | undefined, id: string) {
  return (name ?? id).split(' @ ')[0]
}

function openInMA(groupId: string) {
  const baseUrl = bridge.bridge?.ma_web_url
  if (baseUrl) openExternal(`${baseUrl.replace(/\/$/, '')}/#/player/${encodeURIComponent(groupId)}`)
}

async function discoverGroups() {
  await ma.refresh()
}

onMounted(() => {
  ma.fetchGroups()
})
</script>

<template>
  <div class="space-y-4">
    <template v-if="ma.groups.length > 0">
      <SbCard v-for="group in ma.groups" :key="group.id" padding="none">
        <div class="flex items-center gap-3 px-4 pt-3">
          <Users class="size-5 shrink-0 text-text-secondary" aria-hidden="true" />
          <h2 class="min-w-0 flex-1 truncate font-medium text-text-primary">{{ group.name }}</h2>
          <button
            v-if="bridge.bridge?.ma_web_url"
            type="button"
            class="inline-flex size-10 items-center justify-center rounded-full text-text-secondary transition-colors hover:bg-surface-secondary hover:text-text-primary"
            :title="t('ma.openInMA')"
            :aria-label="t('ma.openInMA')"
            @click="openInMA(group.id)"
          >
            <ExternalLink class="size-4" aria-hidden="true" />
          </button>
        </div>
        <ul class="flex flex-wrap gap-1.5 px-4 pt-1 pb-3" :aria-label="t('ma.groups.members')">
          <li v-for="member in group.members" :key="member.id">
            <SbBadge :tone="member.state === 'playing' ? 'success' : 'neutral'" size="sm" dot :title="member.name ?? member.id">
              {{ memberName(member.name, member.id) }}
            </SbBadge>
          </li>
        </ul>
        <div class="border-t border-border px-4 py-3">
          <MaNowPlaying :group-id="group.id" />
        </div>
      </SbCard>
    </template>

    <SbEmptyState
      v-else
      :title="t('ma.groups.empty')"
      :description="t('ma.groups.emptyDesc')"
    >
      <template #icon>
        <Users class="h-16 w-16" aria-hidden="true" />
      </template>
      <template #action>
        <SbButton variant="primary" :loading="ma.discovering" @click="discoverGroups">
          {{ t('ma.groups.discover') }}
        </SbButton>
      </template>
    </SbEmptyState>
  </div>
</template>
