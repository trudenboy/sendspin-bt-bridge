import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import ArtworkThumb from '@/components/playback/ArtworkThumb.vue'
import en from '@/i18n/locales/en.json'

function mountThumb(url: string | null) {
  return mount(ArtworkThumb, {
    props: { url, alt: 'Song' },
    global: { plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })] },
    attachTo: document.body,
  })
}

describe('ArtworkThumb', () => {
  it('shows the artwork and enlarges it on click, closing on Escape', async () => {
    const w = mountThumb('/api/v1/ma/artwork?sig=1')
    expect(w.findAll('img')).toHaveLength(1)
    expect(w.find('img').attributes('src')).toBe('/api/v1/ma/artwork?sig=1')
    await w.find('button').trigger('click')
    expect(w.findAll('img')).toHaveLength(2)
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await w.vm.$nextTick()
    expect(w.findAll('img')).toHaveLength(1)
    w.unmount()
  })

  it('falls back to a music icon when the image fails, and does not enlarge it', async () => {
    const w = mountThumb('/api/v1/ma/artwork?sig=broken')
    await w.find('img').trigger('error')
    expect(w.find('img').exists()).toBe(false)
    expect(w.find('svg').exists()).toBe(true)
    await w.find('button').trigger('click')
    expect(w.find('img').exists()).toBe(false)
    w.unmount()
  })

  it('shows the music icon without a url', () => {
    const w = mountThumb(null)
    expect(w.find('img').exists()).toBe(false)
    expect(w.find('svg').exists()).toBe(true)
    w.unmount()
  })
})
