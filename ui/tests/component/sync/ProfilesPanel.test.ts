import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { createPinia, setActivePinia } from 'pinia'
import ProfilesPanel from '@/components/sync/ProfilesPanel.vue'
import { useSyncStore } from '@/stores/sync'
import en from '@/i18n/en.json'

vi.mock('@/api/sync', () => ({ listProfiles: vi.fn(), applyProfile: vi.fn(), captureProfile: vi.fn(), patchProfile: vi.fn(), deleteProfile: vi.fn(), getSyncGroup: vi.fn() }))
import * as api from '@/api/sync'
vi.mock('@/composables/useConfirm', () => ({ confirmDialog: vi.fn().mockResolvedValue(true) }))
import { confirmDialog } from '@/composables/useConfirm'

const i18n = createI18n({ legacy: false, locale: 'en', messages: { en } })
const members = [{ ma_player_id: 'a', name: 'A', origin: 'bridge', adjustable: true, delay_ms: 0 }, { ma_player_id: 'b', name: 'B', origin: 'sendspin', adjustable: true, delay_ms: 50 }]

describe('ProfilesPanel', () => {
  beforeEach(() => { setActivePinia(createPinia()); vi.clearAllMocks(); vi.mocked(confirmDialog).mockResolvedValue(true) })
  afterEach(() => { document.body.innerHTML = '' })

  it('lists profiles, shows the diff before applying and reports skipped members', async () => {
    const store = useSyncStore(); store.group = { id: 'g', name: 'G', session_id: null, members } as never
    vi.mocked(api.listProfiles).mockResolvedValue([{ id: 'p1', name: 'Evening', group_id: 'g', kind: 'device', delays: { a: 120, b: 0, gone: 30 }, created_at: '2026-10-15T10:00:00Z', notes: '' }] as never)
    vi.mocked(api.applyProfile).mockResolvedValue({ applied: { a: 120, b: 0 }, skipped: ['gone'] })
    const w = mount(ProfilesPanel, { global: { plugins: [i18n] }, props: { groupId: 'g' }, attachTo: document.body })
    await flushPromises()
    expect(w.text()).toContain('Evening')
    await w.find('[data-apply="p1"]').trigger('click'); await flushPromises()
    const dialog = document.body.querySelector('[data-profile-diff]')!
    expect(dialog.textContent).toContain('A'); expect(dialog.textContent).toContain('0 → 120'); expect(dialog.textContent).toContain('gone')
    await (dialog.querySelector('[data-confirm]') as HTMLElement).click(); await flushPromises()
    expect(api.applyProfile).toHaveBeenCalledWith('p1')
    expect(document.body.textContent).toContain('1 member skipped')
    const skipped = w.get('[data-skipped]')
    expect(skipped.text()).toContain('gone')
    expect(skipped.element.closest('[aria-live]')).not.toBeNull()
    w.unmount()
  })

  it('does not apply when the diff is cancelled', async () => {
    const store = useSyncStore(); store.group = { id: 'g', name: 'G', session_id: null, members } as never
    vi.mocked(api.listProfiles).mockResolvedValue([{ id: 'p1', name: 'Evening', group_id: 'g', kind: 'device', delays: { a: 120 }, created_at: '', notes: '' }] as never)
    const w = mount(ProfilesPanel, { global: { plugins: [i18n] }, props: { groupId: 'g' }, attachTo: document.body })
    await flushPromises()
    await w.find('[data-apply="p1"]').trigger('click'); await flushPromises()
    await (document.body.querySelector('[data-profile-diff] [data-cancel]') as HTMLElement).click(); await flushPromises()
    expect(api.applyProfile).not.toHaveBeenCalled()
    expect(document.body.querySelector('[data-profile-diff]')).toBeNull()
    w.unmount()
  })

  it('saves the current delays as a profile', async () => {
    const store = useSyncStore(); store.group = { id: 'g', name: 'G', session_id: null, members } as never
    vi.mocked(api.listProfiles).mockResolvedValue([]); vi.mocked(api.captureProfile).mockResolvedValue({ id: 'p2', name: 'Now', group_id: 'g', kind: 'device', delays: { a: 0, b: 50 }, created_at: '', notes: '' } as never)
    const w = mount(ProfilesPanel, { global: { plugins: [i18n] }, props: { groupId: 'g' }, attachTo: document.body }); await flushPromises()
    await w.find('[data-save-current]').trigger('click')
    await (document.body.querySelector('[data-profile-name]') as HTMLInputElement).focus()
    ;(document.body.querySelector('[data-profile-name]') as HTMLInputElement).value = 'Now'
    document.body.querySelector('[data-profile-name]')!.dispatchEvent(new Event('input'))
    await (document.body.querySelector('[data-profile-save]') as HTMLElement).click(); await flushPromises()
    expect(api.captureProfile).toHaveBeenCalledWith('g', { name: 'Now', kind: 'device' })
    expect(w.text()).toContain('Now')
    w.unmount()
  })

  it('passes notes when they are given with the name', async () => {
    const store = useSyncStore(); store.group = { id: 'g', name: 'G', session_id: null, members } as never
    vi.mocked(api.listProfiles).mockResolvedValue([]); vi.mocked(api.captureProfile).mockResolvedValue({ id: 'p2', name: 'Now', group_id: 'g', kind: 'device', delays: {}, created_at: '', notes: 'party' } as never)
    const w = mount(ProfilesPanel, { global: { plugins: [i18n] }, props: { groupId: 'g' }, attachTo: document.body }); await flushPromises()
    await w.find('[data-save-current]').trigger('click')
    const name = document.body.querySelector('[data-profile-name]') as HTMLInputElement
    name.value = 'Now'; name.dispatchEvent(new Event('input'))
    const notes = document.body.querySelector('[data-profile-notes]') as HTMLInputElement
    notes.value = 'party'; notes.dispatchEvent(new Event('input'))
    await (document.body.querySelector('[data-profile-save]') as HTMLElement).click(); await flushPromises()
    expect(api.captureProfile).toHaveBeenCalledWith('g', { name: 'Now', kind: 'device', notes: 'party' })
    w.unmount()
  })

  it('does not save without a name', async () => {
    const store = useSyncStore(); store.group = { id: 'g', name: 'G', session_id: null, members } as never
    vi.mocked(api.listProfiles).mockResolvedValue([])
    const w = mount(ProfilesPanel, { global: { plugins: [i18n] }, props: { groupId: 'g' }, attachTo: document.body }); await flushPromises()
    await w.find('[data-save-current]').trigger('click')
    const name = document.body.querySelector('[data-profile-name]') as HTMLInputElement
    name.value = '   '; name.dispatchEvent(new Event('input'))
    await (document.body.querySelector('[data-profile-save]') as HTMLElement).click(); await flushPromises()
    expect(api.captureProfile).not.toHaveBeenCalled()
    const input = document.body.querySelector('[data-profile-name]') as HTMLInputElement
    expect(input.getAttribute('aria-invalid')).toBe('true')
    expect(document.body.querySelector('[data-profile-name-error]')!.textContent).toContain(en.sync.profiles.nameRequired)
    expect(document.activeElement).toBe(input)
    w.unmount()
  })

  it('renames a profile inline with Enter', async () => {
    const store = useSyncStore(); store.group = { id: 'g', name: 'G', session_id: null, members } as never
    vi.mocked(api.listProfiles).mockResolvedValue([{ id: 'p1', name: 'Evening', group_id: 'g', kind: 'device', delays: {}, created_at: '', notes: '' }] as never)
    vi.mocked(api.patchProfile).mockResolvedValue({ id: 'p1', name: 'Late', group_id: 'g', kind: 'device', delays: {}, created_at: '', notes: '' } as never)
    const w = mount(ProfilesPanel, { global: { plugins: [i18n] }, props: { groupId: 'g' }, attachTo: document.body }); await flushPromises()
    await w.get('[data-rename="p1"]').trigger('click')
    const input = w.get('[data-rename-input="p1"]')
    expect(document.activeElement).toBe(input.element)
    await input.setValue('Late')
    await input.trigger('keydown', { key: 'Enter' }); await flushPromises()
    expect(api.patchProfile).toHaveBeenCalledWith('p1', { name: 'Late' })
    expect(w.text()).toContain('Late')
    expect(w.find('[data-rename-input="p1"]').exists()).toBe(false)
    expect(document.activeElement).toBe(w.get('[data-rename="p1"]').element)
    w.unmount()
  })

  it('cancels a rename with Escape', async () => {
    const store = useSyncStore(); store.group = { id: 'g', name: 'G', session_id: null, members } as never
    vi.mocked(api.listProfiles).mockResolvedValue([{ id: 'p1', name: 'Evening', group_id: 'g', kind: 'device', delays: {}, created_at: '', notes: '' }] as never)
    const w = mount(ProfilesPanel, { global: { plugins: [i18n] }, props: { groupId: 'g' }, attachTo: document.body }); await flushPromises()
    await w.get('[data-rename="p1"]').trigger('click')
    await w.get('[data-rename-input="p1"]').setValue('Late')
    await w.get('[data-rename-input="p1"]').trigger('keydown', { key: 'Escape' }); await flushPromises()
    expect(api.patchProfile).not.toHaveBeenCalled()
    expect(w.text()).toContain('Evening')
    expect(document.activeElement).toBe(w.get('[data-rename="p1"]').element)
    w.unmount()
  })

  it('deletes a profile only after confirmation', async () => {
    const store = useSyncStore(); store.group = { id: 'g', name: 'G', session_id: null, members } as never
    vi.mocked(api.listProfiles).mockResolvedValue([{ id: 'p1', name: 'Evening', group_id: 'g', kind: 'device', delays: {}, created_at: '', notes: '' }] as never)
    vi.mocked(api.deleteProfile).mockResolvedValue(undefined)
    const w = mount(ProfilesPanel, { global: { plugins: [i18n] }, props: { groupId: 'g' }, attachTo: document.body }); await flushPromises()
    vi.mocked(confirmDialog).mockResolvedValueOnce(false)
    await w.get('[data-delete="p1"]').trigger('click'); await flushPromises()
    expect(api.deleteProfile).not.toHaveBeenCalled()
    await w.get('[data-delete="p1"]').trigger('click'); await flushPromises()
    expect(confirmDialog).toHaveBeenCalledTimes(2)
    expect(api.deleteProfile).toHaveBeenCalledWith('p1')
    expect(w.find('[data-profile="p1"]').exists()).toBe(false)
    w.unmount()
  })

  it('moves focus to the next profile after a delete, and to the heading after the last one', async () => {
    const store = useSyncStore(); store.group = { id: 'g', name: 'G', session_id: null, members } as never
    const p = (id: string, name: string) => ({ id, name, group_id: 'g', kind: 'device', delays: {}, created_at: '', notes: '' })
    vi.mocked(api.listProfiles).mockResolvedValue([p('p1', 'Evening'), p('p2', 'Morning')] as never)
    vi.mocked(api.deleteProfile).mockResolvedValue(undefined)
    const w = mount(ProfilesPanel, { global: { plugins: [i18n] }, props: { groupId: 'g' }, attachTo: document.body }); await flushPromises()
    await w.get('[data-delete="p1"]').trigger('click'); await flushPromises()
    expect(document.activeElement).toBe(w.get('[data-apply="p2"]').element)
    await w.get('[data-delete="p2"]').trigger('click'); await flushPromises()
    expect(document.activeElement).toBe(w.get('#sync-profiles-title').element)
    w.unmount()
  })

  it('labels players that are not in the group by their player ID', async () => {
    const store = useSyncStore(); store.group = { id: 'g', name: 'G', session_id: null, members } as never
    vi.mocked(api.listProfiles).mockResolvedValue([{ id: 'p1', name: 'Evening', group_id: 'g', kind: 'device', delays: { a: 120, gone: 30 }, created_at: '', notes: '' }] as never)
    vi.mocked(api.applyProfile).mockResolvedValue({ applied: { a: 120 }, skipped: ['b', 'gone'] })
    const w = mount(ProfilesPanel, { global: { plugins: [i18n] }, props: { groupId: 'g' }, attachTo: document.body }); await flushPromises()
    await w.get('[data-apply="p1"]').trigger('click'); await flushPromises()
    expect(document.body.querySelector('[data-not-in-group]')!.textContent).toContain('player ID gone')
    await (document.body.querySelector('[data-confirm]') as HTMLElement).click(); await flushPromises()
    const skipped = w.get('[data-skipped]').text()
    expect(skipped).toContain('2 members skipped')
    expect(skipped).toContain('B')
    expect(skipped).toContain('player ID gone')
    expect(skipped).not.toContain('player ID b')
    w.unmount()
  })

  it('says when there are no profiles yet', async () => {
    const store = useSyncStore(); store.group = { id: 'g', name: 'G', session_id: null, members } as never
    vi.mocked(api.listProfiles).mockResolvedValue([])
    const w = mount(ProfilesPanel, { global: { plugins: [i18n] }, props: { groupId: 'g' } }); await flushPromises()
    expect(w.text()).toContain(en.sync.profiles.empty)
  })
})
