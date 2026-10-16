import { describe, it, expect } from 'vitest'
import { speakerName } from '@/sync/names'

describe('speakerName (sync screens)', () => {
  it('drops the bridge suffix and trailing technical ids', () => {
    expect(speakerName('ENEBY Portable @ 85b1ecde-sendspin-bt-bridge-beta')).toBe('ENEBY Portable')
    expect(speakerName('Web Kiosk (wk_723e2...) [192.168.10.229]')).toBe('Web Kiosk')
  })
  it('keeps names with spaces in brackets and falls back when empty', () => {
    expect(speakerName('Living room (by the sofa)')).toBe('Living room (by the sofa)')
    expect(speakerName('Kitchen (left)')).toBe('Kitchen (left)')
    expect(speakerName('', 'id-1')).toBe('id-1')
    expect(speakerName('[x]', 'id-2')).toBe('[x]')
  })
})
