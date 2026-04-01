import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, VueWrapper, flushPromises } from '@vue/test-utils'
import { nextTick } from 'vue'
import SbDialog from '@/kit/SbDialog.vue'

async function mountDialog(props: Record<string, unknown> = {}, slots: Record<string, string> = {}) {
  const w = mount(SbDialog, {
    props: { modelValue: true, title: 'Test Dialog', ...props },
    global: { stubs: { Teleport: true } },
    slots: { default: 'Dialog body', ...slots },
  })
  await nextTick()
  await nextTick()
  return w
}

describe('SbDialog', () => {
  afterEach(() => {
    document.body.classList.remove('overflow-hidden')
  })

  describe('rendering', () => {
    it('renders when modelValue is true', async () => {
      const w = await mountDialog()
      expect(w.find('[role="dialog"]').exists()).toBe(true)
    })

    it('does not render when modelValue is false', async () => {
      const w = await mountDialog({ modelValue: false })
      expect(w.find('[role="dialog"]').exists()).toBe(false)
    })

    it('renders title', async () => {
      const w = await mountDialog({ title: 'My Title' })
      expect(w.text()).toContain('My Title')
    })

    it('renders body slot', async () => {
      const w = await mountDialog({}, { default: 'Custom body' })
      expect(w.text()).toContain('Custom body')
    })

    it('renders header slot', async () => {
      const w = await mountDialog({}, { header: '<span>Custom Header</span>' })
      expect(w.text()).toContain('Custom Header')
    })

    it('renders footer slot', async () => {
      const w = await mountDialog({}, { footer: '<button>Save</button>' })
      expect(w.text()).toContain('Save')
    })
  })

  describe('sizes', () => {
    it.each(['sm', 'md', 'lg', 'xl'] as const)('applies %s size class', async (size) => {
      const w = await mountDialog({ size })
      expect(w.find('[role="dialog"]').classes()).toContain(`max-w-${size}`)
    })

    it('defaults to md size', async () => {
      const w = await mountDialog()
      expect(w.find('[role="dialog"]').classes()).toContain('max-w-md')
    })
  })

  describe('ARIA', () => {
    it('has role="dialog"', async () => {
      const w = await mountDialog()
      expect(w.find('[role="dialog"]').exists()).toBe(true)
    })

    it('has aria-modal="true"', async () => {
      const w = await mountDialog()
      expect(w.find('[role="dialog"]').attributes('aria-modal')).toBe('true')
    })

    it('has aria-labelledby pointing to title', async () => {
      const w = await mountDialog({ title: 'Test' })
      const dialog = w.find('[role="dialog"]')
      const labelledby = dialog.attributes('aria-labelledby')
      expect(labelledby).toBeTruthy()
      const heading = w.find(`#${labelledby}`)
      expect(heading.exists()).toBe(true)
      expect(heading.text()).toBe('Test')
    })
  })

  describe('close behavior', () => {
    it('emits close when close button is clicked', async () => {
      const w = await mountDialog()
      await w.find('[data-testid="dialog-close-btn"]').trigger('click')
      expect(w.emitted('close')).toBeTruthy()
    })

    it('emits update:modelValue when close button is clicked', async () => {
      const w = await mountDialog()
      await w.find('[data-testid="dialog-close-btn"]').trigger('click')
      expect(w.emitted('update:modelValue')?.[0]).toEqual([false])
    })

    it('closes on backdrop click', async () => {
      const w = await mountDialog()
      await w.find('[data-testid="dialog-backdrop"]').trigger('click')
      expect(w.emitted('update:modelValue')?.[0]).toEqual([false])
    })

    it('does not close on backdrop click when persistent', async () => {
      const w = await mountDialog({ persistent: true })
      await w.find('[data-testid="dialog-backdrop"]').trigger('click')
      expect(w.emitted('update:modelValue')).toBeFalsy()
    })

    it('hides close button when closable is false', async () => {
      const w = await mountDialog({ closable: false })
      expect(w.find('[data-testid="dialog-close-btn"]').exists()).toBe(false)
    })
  })

  describe('keyboard', () => {
    it('closes on ESC key', async () => {
      const w = await mountDialog()
      const event = new KeyboardEvent('keydown', { key: 'Escape' })
      document.dispatchEvent(event)
      expect(w.emitted('update:modelValue')?.[0]).toEqual([false])
    })

    it('does not close on ESC when persistent', async () => {
      const w = await mountDialog({ persistent: true })
      const event = new KeyboardEvent('keydown', { key: 'Escape' })
      document.dispatchEvent(event)
      expect(w.emitted('update:modelValue')).toBeFalsy()
    })
  })

  describe('body scroll lock', () => {
    it('adds overflow-hidden to body when open', async () => {
      await mountDialog()
      expect(document.body.classList.contains('overflow-hidden')).toBe(true)
    })

    it('removes overflow-hidden when unmounted', async () => {
      const w = await mountDialog()
      w.unmount()
      expect(document.body.classList.contains('overflow-hidden')).toBe(false)
    })
  })
})
