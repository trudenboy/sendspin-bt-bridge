import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('@/api/updates', () => ({
  getUpdateInfo: vi.fn(),
  startUpdateCheck: vi.fn(),
  applyUpdate: vi.fn(),
}))

vi.mock('@/composables/useIngress', () => ({
  useIngress: () => ({ basePath: '', apiBase: '' }),
}))

import {
  getUpdateInfo,
  startUpdateCheck,
  applyUpdate,
} from '@/api/updates'
import { useUpdateStore } from '@/stores/update'
import type { Job } from '@/api/types'

const job = (status: Job['status'], extra: Record<string, unknown> = {}) =>
  ({ id: 'j', kind: 'updates.check', status, created_at: '', updated_at: '', progress: {}, ...extra }) as Job

describe('useUpdateStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('has correct initial state', () => {
    const store = useUpdateStore()
    expect(store.updateAvailable).toBe(false)
    expect(store.latestVersion).toBeNull()
    expect(store.releaseNotes).toBeNull()
    expect(store.checking).toBe(false)
    expect(store.applying).toBe(false)
    expect(store.loading).toBe(false)
    expect(store.showDialog).toBe(false)
    expect(store.error).toBeNull()
    expect(store.runtime).toBe('unknown')
    expect(store.updateMethod).toBe('manual')
    expect(store.channel).toBe('stable')
  })

  describe('fetchInfo', () => {
    it('fetches and stores update info', async () => {
      const mockInfo = {
        update_available: true,
        runtime: 'systemd' as const,
        auto_update: false,
        channel: 'stable',
        version: '2.53.0',
        tag: 'v2.53.0',
        url: 'https://github.com/example/releases/tag/v2.53.0',
        body: '### Fixed\n- Bug fix',
        update_method: 'one_click' as const,
        instructions: 'Click Update Now',
      }
      vi.mocked(getUpdateInfo).mockResolvedValue(mockInfo)

      const store = useUpdateStore()
      await store.fetchInfo()

      expect(store.updateAvailable).toBe(true)
      expect(store.latestVersion).toBe('2.53.0')
      expect(store.releaseNotes).toBe('### Fixed\n- Bug fix')
      expect(store.runtime).toBe('systemd')
      expect(store.updateMethod).toBe('one_click')
      expect(store.loading).toBe(false)
      expect(store.error).toBeNull()
    })

    it('handles fetch error gracefully', async () => {
      vi.mocked(getUpdateInfo).mockRejectedValue(new Error('Network error'))

      const store = useUpdateStore()
      await store.fetchInfo()

      expect(store.error).toBe('Network error')
      expect(store.loading).toBe(false)
      expect(store.info).toBeNull()
    })

    it('sets loading during fetch', async () => {
      let resolvePromise: (v: unknown) => void
      vi.mocked(getUpdateInfo).mockReturnValue(
        new Promise((r) => {
          resolvePromise = r
        }),
      )

      const store = useUpdateStore()
      const promise = store.fetchInfo()
      expect(store.loading).toBe(true)

      resolvePromise!({
        update_available: false,
        runtime: 'docker',
        auto_update: false,
        channel: 'stable',
        update_method: 'manual',
      })
      await promise
      expect(store.loading).toBe(false)
    })
  })

  describe('checkForUpdates', () => {
    it('follows the check job and opens the dialog when a release is newer', async () => {
      vi.mocked(startUpdateCheck).mockResolvedValue(job('succeeded', { result: { update_available: true } }))
      vi.mocked(getUpdateInfo).mockResolvedValue({
        update_available: true,
        runtime: 'systemd',
        auto_update: false,
        channel: 'stable',
        version: '2.53.0',
        update_method: 'one_click',
      })

      const store = useUpdateStore()
      await store.checkForUpdates()

      expect(startUpdateCheck).toHaveBeenCalledWith(undefined)
      expect(store.updateAvailable).toBe(true)
      expect(store.showDialog).toBe(true)
      expect(store.checking).toBe(false)
    })

    it('passes the channel through', async () => {
      vi.mocked(startUpdateCheck).mockResolvedValue(job('succeeded'))
      vi.mocked(getUpdateInfo).mockResolvedValue({
        update_available: false,
        runtime: 'docker',
        auto_update: false,
        channel: 'beta',
        update_method: 'manual',
      })
      await useUpdateStore().checkForUpdates('beta')
      expect(startUpdateCheck).toHaveBeenCalledWith('beta')
    })

    it('says in words that the bridge is up to date', async () => {
      const { useNotificationStore } = await import('@/stores/notifications')
      vi.mocked(startUpdateCheck).mockResolvedValue(job('succeeded'))
      vi.mocked(getUpdateInfo).mockResolvedValue({
        update_available: false,
        runtime: 'docker',
        auto_update: false,
        channel: 'stable',
        update_method: 'manual',
      })
      await useUpdateStore().checkForUpdates()
      const messages = useNotificationStore().toasts.map((n) => n.message)
      expect(messages).toContain('You are running the latest version')
    })

    it('reports a failed check', async () => {
      vi.mocked(startUpdateCheck).mockResolvedValue(
        job('failed', { error: { code: 'github_unreachable', detail: 'Rate limited', status: 502 } }),
      )
      const store = useUpdateStore()
      await store.checkForUpdates()
      expect(store.error).toBe('Rate limited')
      expect(store.checking).toBe(false)
      expect(store.showDialog).toBe(false)
    })

    it('handles network error', async () => {
      vi.mocked(startUpdateCheck).mockRejectedValue(new Error('offline'))
      const store = useUpdateStore()
      await store.checkForUpdates()
      expect(store.error).toBe('offline')
      expect(store.checking).toBe(false)
    })
  })

  describe('doApplyUpdate', () => {
    it('applies update successfully', async () => {
      vi.mocked(applyUpdate).mockResolvedValue({
        success: true,
        message: 'Upgrade started.',
        started: true,
      })

      const store = useUpdateStore()
      store.info = {
        update_available: true,
        runtime: 'systemd',
        auto_update: false,
        channel: 'stable',
        version: '2.53.0',
        tag: 'v2.53.0',
        update_method: 'one_click',
      }
      store.showDialog = true

      const result = await store.doApplyUpdate()

      expect(applyUpdate).toHaveBeenCalledWith('v2.53.0', 'stable')
      expect(result?.started).toBe(true)
      expect(store.showDialog).toBe(false)
      expect(store.applying).toBe(false)
    })

    it('reports a refused update (the problem detail)', async () => {
      vi.mocked(applyUpdate).mockRejectedValue(new Error('upgrade.sh not found'))
      const store = useUpdateStore()
      store.info = { update_available: true, runtime: 'systemd', auto_update: false, channel: 'stable', update_method: 'one_click' }
      expect(await store.doApplyUpdate()).toBeNull()
      expect(store.error).toBe('upgrade.sh not found')
      expect(store.applying).toBe(false)
    })

    it('handles network error during apply', async () => {
      vi.mocked(applyUpdate).mockRejectedValue(new Error('timeout'))

      const store = useUpdateStore()
      store.info = {
        update_available: true,
        runtime: 'systemd',
        auto_update: false,
        channel: 'stable',
        update_method: 'one_click',
      }

      const result = await store.doApplyUpdate()

      expect(result).toBeNull()
      expect(store.error).toBe('timeout')
      expect(store.applying).toBe(false)
    })
  })

  describe('openDialog', () => {
    it('sets showDialog to true', () => {
      const store = useUpdateStore()
      expect(store.showDialog).toBe(false)
      store.openDialog()
      expect(store.showDialog).toBe(true)
    })
  })
})
