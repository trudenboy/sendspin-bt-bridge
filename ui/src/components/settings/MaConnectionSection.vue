<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useBridgeStore } from '@/stores/bridge'
import { SbBadge, SbButton } from '@/kit'
import MaLoginFlow from '@/components/ma/MaLoginFlow.vue'
import SettingsRow from './SettingsRow.vue'

const { t } = useI18n()
const bridge = useBridgeStore()
const signingIn = ref(false)
</script>

<template>
  <div>
    <SettingsRow :label="t('settings.parts.maConnection.label')" :help="bridge.bridge?.ma_web_url || t('settings.parts.maConnection.help')">
      <div class="flex items-center gap-2">
        <SbBadge :tone="bridge.maConnected ? 'success' : 'neutral'" dot>
          {{ bridge.maConnected ? t('dashboard.maConnected') : t('dashboard.maDisconnected') }}
        </SbBadge>
        <SbButton variant="outline" size="sm" @click="signingIn = !signingIn">
          {{ bridge.maConnected ? t('settings.parts.maConnection.change') : t('settings.parts.maConnection.signIn') }}
        </SbButton>
      </div>
    </SettingsRow>
    <div v-if="signingIn || !bridge.maConnected" class="pb-4">
      <MaLoginFlow />
    </div>
  </div>
</template>
