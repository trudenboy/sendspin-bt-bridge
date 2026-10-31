import { describe, it, expect, vi, afterEach } from 'vitest'
import { resetClient } from '@/api/client'
import { getHardware, getHardwareBluetoothDevice, getHardwareController } from '@/api/hardware'

function stubFetch(body: unknown = {}) {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } }),
  )
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

const urlOf = (fetchMock: ReturnType<typeof vi.fn>) => {
  const [input] = fetchMock.mock.calls[0]! as [Request | string]
  if (typeof input === 'string') return input
  const url = new URL(input.url)
  return url.pathname + url.search
}

describe('hardware API', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    resetClient()
  })

  it('reads the aggregate snapshot', async () => {
    const fetchMock = stubFetch({ controllers: [] })
    expect(await getHardware()).toEqual({ controllers: [] })
    expect(urlOf(fetchMock)).toBe('/api/v1/hardware')
  })

  it('reads one controller by its hci name', async () => {
    const fetchMock = stubFetch({ id: 'hci0' })
    await getHardwareController('hci0')
    expect(urlOf(fetchMock)).toBe('/api/v1/hardware/controllers/hci0')
  })

  it('reads one Bluetooth device by MAC, encoded for the path', async () => {
    const fetchMock = stubFetch({ mac: 'AA:BB:CC:DD:EE:FF' })
    await getHardwareBluetoothDevice('AA:BB:CC:DD:EE:FF')
    expect(urlOf(fetchMock)).toBe('/api/v1/hardware/bluetooth/AA%3ABB%3ACC%3ADD%3AEE%3AFF')
  })
})
