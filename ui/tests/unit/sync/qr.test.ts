import { describe, it, expect } from 'vitest'
import { qrPath } from '@/sync/qr'

describe('qrPath', () => {
  it('draws a QR code with its three finder patterns', () => {
    const { size, d } = qrPath('http://192.168.1.5:8080/sync/capture/abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG?lang=ru')
    expect(size).toBeGreaterThanOrEqual(25)
    // the top-left corner of each finder pattern is dark
    for (const [x, y] of [[0, 0], [size - 7, 0], [0, size - 7]]) expect(d).toContain(`M${x} ${y}h1v1h-1z`)
    expect(d).not.toContain(`M7 7h1`)  // the separator next to the top-left finder is light
  })
})
