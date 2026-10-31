import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { getHardware } from '@/api/hardware'
import { forgetBtDevice } from '@/api/bluetooth'
import { ApiError } from '@/api/client'
import { useBluetoothStore } from '@/stores/bluetooth'
import { useNotificationStore } from '@/stores/notifications'
import en from '@/i18n/locales/en.json'
import { btDevice, mountView, snapshot } from './support'

const confirm = vi.hoisted(() => ({ confirmDialog: vi.fn() }))
vi.mock('@/composables/useConfirm', () => ({ confirmDialog: confirm.confirmDialog }))
vi.mock('@/composables/useEventStream', () => ({
  DEFAULT_EVENT_TYPES: ['status', 'job'],
  useEventStream: () => ({ connect: vi.fn(), disconnect: vi.fn() }),
}))
vi.mock('@/api/hardware', () => ({ getHardware: vi.fn() }))
vi.mock('@/api/bluetooth', () => ({ setAdapterPower: vi.fn(), forgetBtDevice: vi.fn() }))

let wrapper: ReturnType<typeof mountView> | null = null

const toast = (message: string) => useNotificationStore().toasts.some((entry) => entry.message === message)

describe('Hardware page: device actions', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    confirm.confirmDialog.mockResolvedValue(true)
  })

  afterEach(() => {
    wrapper?.unmount()
    wrapper = null
    document.body.innerHTML = ''
  })

  it('adds a paired device that is not a player as a speaker on its controller', async () => {
    vi.mocked(getHardware).mockResolvedValue(snapshot({ bluetooth_devices: [btDevice({ player_id: null })] }) as never)
    const add = vi.spyOn(useBluetoothStore(), 'addToBridge').mockResolvedValue(true)

    wrapper = mountView()
    await flushPromises()

    await wrapper.get('[data-add-speaker]').trigger('click')
    await flushPromises()

    expect(add).toHaveBeenCalledWith('FC:58:FA:EB:08:6C', 'ENEBY20', 'hci0')
    expect(getHardware).toHaveBeenCalledTimes(2)
    expect(toast(en.addSpeaker.addedTitle.replace('{name}', 'ENEBY20'))).toBe(true)
  })

  it('offers no add button once the device backs a player', async () => {
    vi.mocked(getHardware).mockResolvedValue(snapshot() as never)

    wrapper = mountView()
    await flushPromises()

    expect(wrapper.find('[data-add-speaker]').exists()).toBe(false)
  })

  it('asks before forgetting a bond and names the player it backs', async () => {
    vi.mocked(getHardware).mockResolvedValue(snapshot() as never)

    wrapper = mountView()
    await flushPromises()

    await wrapper.get('[data-forget-bond]').trigger('click')
    await flushPromises()

    expect(confirm.confirmDialog).toHaveBeenCalledOnce()
    const options = confirm.confirmDialog.mock.calls[0]![0]
    expect(options.consequences).toContain(en.device.actions.forgetConfirm)
    expect(options.consequences.join(' ')).toContain('eneby20')
    expect(forgetBtDevice).toHaveBeenCalledWith('FC:58:FA:EB:08:6C')
    expect(getHardware).toHaveBeenCalledTimes(2)
    expect(toast(en.hardware.forgotBond.replace('{name}', 'ENEBY20'))).toBe(true)
  })

  it('surfaces the server code and message when the bond backs a player', async () => {
    vi.mocked(getHardware).mockResolvedValue(snapshot() as never)
    vi.mocked(forgetBtDevice).mockRejectedValue(
      new ApiError({
        status: 409,
        code: 'bond_in_use_by_player',
        detail: 'The bond backs a player; remove the player first.',
      }),
    )

    wrapper = mountView()
    await flushPromises()

    await wrapper.get('[data-forget-bond]').trigger('click')
    await flushPromises()

    const errors = useNotificationStore().toasts.filter((entry) => entry.type === 'error')
    expect(errors).toHaveLength(1)
    expect(errors[0]!.message).toContain('bond_in_use_by_player')
    expect(errors[0]!.message).toContain('The bond backs a player; remove the player first.')
  })
})
