import { describe, it, expect } from 'vitest'
import { speakerName } from '@/utils/speakerName'

describe('speakerName', () => {
  it('drops the bridge suffix', () => {
    expect(speakerName('ENEBY Portable @ HP-ProDesk')).toBe('ENEBY Portable')
  })
  it('keeps an @ that is part of the name', () => {
    expect(speakerName('Kitchen @ Home @ bridge')).toBe('Kitchen @ Home')
    expect(speakerName('@Desk')).toBe('@Desk')
  })
  it('falls back when there is no name', () => {
    expect(speakerName(null, 'AA:BB')).toBe('AA:BB')
    expect(speakerName('  ', 'AA:BB')).toBe('AA:BB')
  })
})
