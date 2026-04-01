import { describe, it, expect, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import SbDrawer from '@/kit/SbDrawer.vue'

async function mountDrawer(props: Record<string, unknown> = {}, slots: Record<string, string> = {}) {
  const w = mount(SbDrawer, {
    props: { modelValue: true, title: 'Test Drawer', ...props },
    global: { stubs: { Teleport: true } },
    slots: { default: 'Drawer body', ...slots },
  })
  await nextTick()
  await nextTick()
  return w
}

describe('SbDrawer', () => {
  afterEach(() => {
    document.body.classList.remove('overflow-hidden')
  })

  describe('rendering', () => {
    it('renders when modelValue is true', async () => {
      const w = await mountDrawer()
      expect(w.find('[role="dialog"]').exists()).toBe(true)
    })

    it('does not render when modelValue is false', async () => {
      const w = await mountDrawer({ modelValue: false })
      expect(w.find('[role="dialog"]').exists()).toBe(false)
    })

    it('renders title', async () => {
      const w = await mountDrawer({ title: 'Settings' })
      expect(w.text()).toContain('Settings')
    })

    it('renders body slot', async () => {
      const w = await mountDrawer({}, { default: 'Drawer content' })
      expect(w.text()).toContain('Drawer content')
    })

    it('renders header slot', async () => {
      const w = await mountDrawer({}, { header: '<span>Custom Header</span>' })
      expect(w.text()).toContain('Custom Header')
    })

    it('renders footer slot', async () => {
      const w = await mountDrawer({}, { footer: '<button>Apply</button>' })
      expect(w.text()).toContain('Apply')
    })
  })

  describe('side positioning', () => {
    it('defaults to right side', async () => {
      const w = await mountDrawer()
      expect(w.find('[role="dialog"]').classes()).toContain('right-0')
    })

    it('positions on left when side="left"', async () => {
      const w = await mountDrawer({ side: 'left' })
      expect(w.find('[role="dialog"]').classes()).toContain('left-0')
    })

    it('positions on right when side="right"', async () => {
      const w = await mountDrawer({ side: 'right' })
      expect(w.find('[role="dialog"]').classes()).toContain('right-0')
    })
  })

  describe('width', () => {
    it('applies default max-w-md width', async () => {
      const w = await mountDrawer()
      expect(w.find('[role="dialog"]').classes()).toContain('max-w-md')
    })

    it('applies custom width class', async () => {
      const w = await mountDrawer({ width: 'max-w-lg' })
      expect(w.find('[role="dialog"]').classes()).toContain('max-w-lg')
    })
  })

  describe('ARIA', () => {
    it('has role="dialog"', async () => {
      const w = await mountDrawer()
      expect(w.find('[role="dialog"]').exists()).toBe(true)
    })

    it('has aria-modal="true"', async () => {
      const w = await mountDrawer()
      expect(w.find('[role="dialog"]').attributes('aria-modal')).toBe('true')
    })

    it('has aria-labelledby pointing to title', async () => {
      const w = await mountDrawer({ title: 'Settings' })
      const dialog = w.find('[role="dialog"]')
      const labelledby = dialog.attributes('aria-labelledby')
      expect(labelledby).toBeTruthy()
      const heading = w.find(`#${labelledby}`)
      expect(heading.exists()).toBe(true)
      expect(heading.text()).toBe('Settings')
    })
  })

  describe('close behavior', () => {
    it('emits close on close button click', async () => {
      const w = await mountDrawer()
      await w.find('[data-testid="drawer-close-btn"]').trigger('click')
      expect(w.emitted('close')).toBeTruthy()
    })

    it('emits update:modelValue on close button click', async () => {
      const w = await mountDrawer()
      await w.find('[data-testid="drawer-close-btn"]').trigger('click')
      expect(w.emitted('update:modelValue')?.[0]).toEqual([false])
    })

    it('closes on backdrop click', async () => {
      const w = await mountDrawer()
      await w.find('[data-testid="drawer-backdrop"]').trigger('click')
      expect(w.emitted('update:modelValue')?.[0]).toEqual([false])
    })

    it('hides close button when closable is false', async () => {
      const w = await mountDrawer({ closable: false })
      expect(w.find('[data-testid="drawer-close-btn"]').exists()).toBe(false)
    })

    it('does not emit close when closable is false and close button clicked', async () => {
      const w = await mountDrawer({ closable: false })
      expect(w.find('[data-testid="drawer-close-btn"]').exists()).toBe(false)
    })
  })

  describe('keyboard', () => {
    it('closes on ESC key', async () => {
      const w = await mountDrawer()
      const event = new KeyboardEvent('keydown', { key: 'Escape' })
      document.dispatchEvent(event)
      expect(w.emitted('update:modelValue')?.[0]).toEqual([false])
    })
  })

  describe('body scroll lock', () => {
    it('adds overflow-hidden to body when open', async () => {
      await mountDrawer()
      expect(document.body.classList.contains('overflow-hidden')).toBe(true)
    })

    it('removes overflow-hidden when unmounted', async () => {
      const w = await mountDrawer()
      w.unmount()
      expect(document.body.classList.contains('overflow-hidden')).toBe(false)
    })
  })
})
