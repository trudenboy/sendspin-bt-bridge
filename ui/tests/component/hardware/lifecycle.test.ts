import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { getHardware } from '@/api/hardware'
import { connectBtDevice, disconnectBtDevice, forgetBtDevice } from '@/api/bluetooth'
import { ApiError } from '@/api/client'
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
vi.mock('@/api/bluetooth', () => ({
  setAdapterPower: vi.fn(),
  forgetBtDevice: vi.fn(),
  connectBtDevice: vi.fn(),
  disconnectBtDevice: vi.fn(),
}))

/**
 * The pairing flow itself is AddSpeakerDialog's, tested next to it; this stub
 * keeps the page's contract visible: it opens the same dialog Home opens and
 * refreshes its inventory when the dialog reports a bond.
 */
vi.mock('@/components/bluetooth/AddSpeakerDialog.vue', () => ({
  default: {
    props: { open: { type: Boolean, default: false } },
    emits: ['update:open', 'paired'],
    template: `<div v-if="open" data-pair-dialog>
      <button type="button" data-paired-result @click="$emit('paired', 'AA:BB:CC:DD:EE:FF')">paired</button>
    </div>`,
  },
}))

let wrapper: ReturnType<typeof mountView> | null = null

const toasts = () => useNotificationStore().toasts
const errorToasts = () => toasts().filter((entry) => entry.type === 'error')
const successTexts = () => toasts().filter((entry) => entry.type === 'success').map((entry) => entry.message)

/** A paired bond with the link down: the card must offer Connect. */
const down = () => snapshot({ bluetooth_devices: [btDevice({ connected: false })] })
/** A bond with the link up: the card must offer Disconnect. */
const up = () => snapshot({ bluetooth_devices: [btDevice({ connected: true })] })

function problem(status: number, code: string, detail: string) {
  return new ApiError({ status, code, detail })
}

describe('Hardware page: Bluetooth lifecycle', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    confirm.confirmDialog.mockResolvedValue(true)
    vi.mocked(getHardware).mockResolvedValue(up() as never)
    vi.mocked(connectBtDevice).mockResolvedValue(undefined as never)
    vi.mocked(disconnectBtDevice).mockResolvedValue(undefined as never)
  })

  afterEach(() => {
    wrapper?.unmount()
    wrapper = null
    document.body.innerHTML = ''
  })

  it('offers Connect for a paired bond that is down and Disconnect for a live link', async () => {
    vi.mocked(getHardware).mockResolvedValue(down() as never)
    wrapper = mountView()
    await flushPromises()

    expect(wrapper.find('[data-connect-device]').exists()).toBe(true)
    expect(wrapper.find('[data-disconnect-device]').exists()).toBe(false)

    wrapper.unmount()
    vi.mocked(getHardware).mockResolvedValue(up() as never)
    wrapper = mountView()
    await flushPromises()

    expect(wrapper.find('[data-disconnect-device]').exists()).toBe(true)
    expect(wrapper.find('[data-connect-device]').exists()).toBe(false)
  })

  it('offers Connect only for a bond, never for an unknown visitor', async () => {
    vi.mocked(getHardware).mockResolvedValue(
      snapshot({ bluetooth_devices: [btDevice({ connected: false, paired: false, bonded: false })] }) as never,
    )
    wrapper = mountView()
    await flushPromises()

    expect(wrapper.find('[data-connect-device]').exists()).toBe(false)
    expect(wrapper.find('[data-disconnect-device]').exists()).toBe(false)
  })

  it('connects the bond, refreshes the snapshot and offers Undo', async () => {
    vi.mocked(getHardware).mockResolvedValue(down() as never)
    wrapper = mountView()
    await flushPromises()

    await wrapper.get('[data-connect-device]').trigger('click')
    await flushPromises()

    expect(connectBtDevice).toHaveBeenCalledWith('FC:58:FA:EB:08:6C')
    expect(getHardware).toHaveBeenCalledTimes(2)
    expect(successTexts()).toContain(en.undo.btConnected.replace('{name}', 'ENEBY20'))
    expect(errorToasts()).toEqual([])
  })

  it('disconnects a live link and refreshes the snapshot', async () => {
    wrapper = mountView()
    await flushPromises()

    await wrapper.get('[data-disconnect-device]').trigger('click')
    await flushPromises()

    expect(disconnectBtDevice).toHaveBeenCalledWith('FC:58:FA:EB:08:6C')
    expect(getHardware).toHaveBeenCalledTimes(2)
    expect(successTexts()).toContain(en.undo.btDisconnected.replace('{name}', 'ENEBY20'))
    expect(errorToasts()).toEqual([])
  })

  it('words a 409 connect_refused as BlueZ refusing, keeping the stable code', async () => {
    vi.mocked(getHardware).mockResolvedValue(down() as never)
    vi.mocked(connectBtDevice).mockRejectedValue(problem(409, 'connect_refused', 'Connection refused'))
    wrapper = mountView()
    await flushPromises()

    await wrapper.get('[data-connect-device]').trigger('click')
    await flushPromises()

    const errors = errorToasts()
    expect(errors).toHaveLength(1)
    expect(errors[0]!.message).toBe(
      en.hardware.connectFailed.replace('{name}', 'ENEBY20').replace('{reason}', 'BlueZ refused (connect_refused)'),
    )
    // A refusal is not a state change: no second read.
    expect(getHardware).toHaveBeenCalledTimes(1)
  })

  it('words a 502 bluetooth_unavailable as the stack not answering', async () => {
    vi.mocked(getHardware).mockResolvedValue(down() as never)
    vi.mocked(connectBtDevice).mockRejectedValue(problem(502, 'bluetooth_unavailable', 'Transport gone'))
    wrapper = mountView()
    await flushPromises()

    await wrapper.get('[data-connect-device]').trigger('click')
    await flushPromises()

    const errors = errorToasts()
    expect(errors).toHaveLength(1)
    expect(errors[0]!.message).toContain('the Bluetooth stack did not answer (bluetooth_unavailable)')
  })

  it('words a 409 disconnect_refused as BlueZ refusing', async () => {
    vi.mocked(disconnectBtDevice).mockRejectedValue(problem(409, 'disconnect_refused', 'Busy'))
    wrapper = mountView()
    await flushPromises()

    await wrapper.get('[data-disconnect-device]').trigger('click')
    await flushPromises()

    const errors = errorToasts()
    expect(errors).toHaveLength(1)
    expect(errors[0]!.message).toContain('BlueZ refused (disconnect_refused)')
  })

  it('surfaces an uncoded failure with its code and the server sentence', async () => {
    vi.mocked(getHardware).mockResolvedValue(down() as never)
    vi.mocked(connectBtDevice).mockRejectedValue(problem(404, 'entity_not_found', 'No such bond'))
    wrapper = mountView()
    await flushPromises()

    await wrapper.get('[data-connect-device]').trigger('click')
    await flushPromises()

    expect(errorToasts()[0]!.message).toContain('entity_not_found: No such bond')
  })

  it('offers the same connect command in the device drawer', async () => {
    vi.mocked(getHardware).mockResolvedValue(down() as never)
    wrapper = mountView()
    await flushPromises()

    await wrapper.get('[data-device-details]').trigger('click')
    await flushPromises()
    await wrapper.get('[data-drawer-connect]').trigger('click')
    await flushPromises()

    expect(connectBtDevice).toHaveBeenCalledWith('FC:58:FA:EB:08:6C')
  })

  it('opens the shared AddSpeakerDialog and refreshes when it reports a pair', async () => {
    wrapper = mountView()
    await flushPromises()

    expect(wrapper.find('[data-pair-dialog]').exists()).toBe(false)

    await wrapper.get('[data-pair-device]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-pair-dialog]').exists()).toBe(true)

    await wrapper.get('[data-paired-result]').trigger('click')
    await flushPromises()

    // The dialog paired a bond; the page re-reads so the device appears now.
    expect(getHardware).toHaveBeenCalledTimes(2)
  })

  it('still asks before forgetting a bond (the action policy is unchanged)', async () => {
    vi.mocked(getHardware).mockResolvedValue(up() as never)
    wrapper = mountView()
    await flushPromises()

    await wrapper.get('[data-forget-bond]').trigger('click')
    await flushPromises()

    expect(confirm.confirmDialog).toHaveBeenCalledOnce()
    expect(forgetBtDevice).toHaveBeenCalledWith('FC:58:FA:EB:08:6C')
  })
})
