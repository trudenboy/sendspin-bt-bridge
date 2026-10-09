<script setup lang="ts">
import { openExternal } from '@/utils/safeUrl'
import { ref, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { useMaStore } from '@/stores/ma'
import { useBridgeStore } from '@/stores/bridge'
import { SbCard, SbBadge, SbButton, SbEmptyState } from '@/kit'
import { Users, ChevronDown, ExternalLink } from 'lucide-vue-next'
import MaNowPlaying from './MaNowPlaying.vue'

const { t } = useI18n()
const ma = useMaStore()
const bridge = useBridgeStore()

const expandedGroupId = ref<string | null>(null)

function toggleGroup(groupId: string) {
  expandedGroupId.value = expandedGroupId.value === groupId ? null : groupId
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
      <SbCard
        v-for="group in ma.groups"
        :key="group.id"
        padding="none"
      >
        <button
          type="button"
          class="flex w-full items-center gap-3 px-4 py-3 text-left"
          :aria-expanded="expandedGroupId === group.id"
          @click="toggleGroup(group.id)"
        >
          <Users class="h-5 w-5 text-text-secondary" aria-hidden="true" />
          <span class="flex-1 font-medium text-text-primary">{{ group.name }}</span>
          <SbBadge tone="info" size="sm">
            {{ group.members.length }} {{ t('ma.groups.members') }}
          </SbBadge>
          <button
            v-if="bridge.bridge?.ma_web_url"
            type="button"
            class="rounded p-1 text-text-secondary transition-colors hover:bg-bg-tertiary hover:text-text-primary"
            :title="t('ma.openInMA')"
            @click.stop="openInMA(group.id)"
          >
            <ExternalLink class="h-4 w-4" aria-hidden="true" />
          </button>
          <ChevronDown
            class="h-4 w-4 text-text-secondary transition-transform"
            :class="{ 'rotate-180': expandedGroupId === group.id }"
            aria-hidden="true"
          />
        </button>

        <!-- Members -->
        <div
          v-if="expandedGroupId === group.id"
          class="border-t border-border px-4 py-3"
        >
          <div class="mb-3 flex flex-wrap gap-1">
            <SbBadge
              v-for="member in group.members"
              :key="member.id"
              :tone="member.state === 'playing' ? 'success' : 'neutral'"
              size="sm"
              dot
            >
              {{ member.name ?? member.id }}
            </SbBadge>
          </div>

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
