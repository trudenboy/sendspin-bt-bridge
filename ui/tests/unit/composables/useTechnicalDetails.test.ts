import { describe, it, expect, beforeEach } from 'vitest'
import {
  technicalDetails,
  reloadTechnicalDetails,
  setTechnicalDetails,
  setTechnicalDetailsDefault,
} from '@/composables/useTechnicalDetails'

describe('useTechnicalDetails', () => {
  beforeEach(() => {
    localStorage.clear()
    reloadTechnicalDetails()
    setTechnicalDetailsDefault(null)
    setTechnicalDetails(null)
  })

  it('is off until anything says otherwise', () => {
    expect(technicalDetails.value).toBe(false)
  })

  it('follows the bridge default when the browser has not chosen', () => {
    setTechnicalDetailsDefault(true)
    expect(technicalDetails.value).toBe(true)
    setTechnicalDetailsDefault(false)
    expect(technicalDetails.value).toBe(false)
  })

  it("the browser's own choice wins over the bridge default and is remembered", () => {
    setTechnicalDetailsDefault(true)
    setTechnicalDetails(false)
    expect(technicalDetails.value).toBe(false)
    expect(localStorage.getItem('sendspin-ui:technical-details')).toBe('0')
    setTechnicalDetailsDefault(false)
    setTechnicalDetails(true)
    expect(technicalDetails.value).toBe(true)
  })

  it('keeps an old "show advanced settings" choice', () => {
    localStorage.setItem('sendspin-ui:settings-advanced', '1')
    reloadTechnicalDetails()
    expect(technicalDetails.value).toBe(true)
  })

  it('works when storage throws', () => {
    const real = Storage.prototype.setItem
    Storage.prototype.setItem = () => {
      throw new Error('blocked')
    }
    try {
      setTechnicalDetails(true)
      expect(technicalDetails.value).toBe(true)
    } finally {
      Storage.prototype.setItem = real
    }
  })
})
