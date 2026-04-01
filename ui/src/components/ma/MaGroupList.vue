<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { useMaStore } from '@/stores/ma'
import { SbCard, SbBadge, SbButton, SbEmptyState } from '@/kit'
import { Users, ChevronDown } from 'lucide-vue-next'
import MaNowPlaying from './MaNowPlaying.vue'

const { t } = useI18n()
const ma = useMaStore()

const expandedGroupId = ref<string | null>(null)

function toggleGroup(groupId: string) {
  expandedGroupId.value = expandedGroupId.value === groupId ? null : groupId
}

async function discoverGroups() {
  await ma.discover()
  await ma.fetchGroups()
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
        :key="group.group_id"
        padding="none"
      >
        <button
          type="button"
          class="flex w-full items-center gap-3 px-4 py-3 text-left"
          :aria-expanded="expandedGroupId === group.group_id"
          @click="toggleGroup(group.group_id)"
        >
          <Users class="h-5 w-5 text-text-secondary" aria-hidden="true" />
          <span class="flex-1 font-medium text-text-primary">{{ group.group_name }}</span>
          <SbBadge tone="info" size="sm">
            {{ group.members.length }} {{ t('ma.groups.members') }}
          </SbBadge>
          <ChevronDown
            class="h-4 w-4 text-text-secondary transition-transform"
            :class="{ 'rotate-180': expandedGroupId === group.group_id }"
            aria-hidden="true"
          />
        </button>

        <!-- Members -->
        <div
          v-if="expandedGroupId === group.group_id"
          class="border-t border-gray-200 px-4 py-3 dark:border-gray-700"
        >
          <div class="mb-3 flex flex-wrap gap-1">
            <SbBadge
              v-for="member in group.members"
              :key="member.player_id"
              :tone="member.state === 'STREAMING' ? 'success' : 'neutral'"
              size="sm"
              dot
            >
              {{ member.player_name }}
            </SbBadge>
          </div>

          <MaNowPlaying :group-id="group.group_id" />
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
