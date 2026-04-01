import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import SbTable from '@/kit/SbTable.vue'

const columns = [
  { key: 'name', label: 'Name', sortable: true },
  { key: 'status', label: 'Status' },
  { key: 'actions', label: 'Actions', align: 'right' as const },
]

const rows = [
  { id: '1', name: 'Device A', status: 'Online' },
  { id: '2', name: 'Device B', status: 'Offline' },
  { id: '3', name: 'Device C', status: 'Online' },
]

describe('SbTable', () => {
  it('renders table headers', () => {
    const wrapper = mount(SbTable, {
      props: { columns, rows },
    })
    const headers = wrapper.findAll('th')
    expect(headers).toHaveLength(3)
    expect(headers[0].text()).toBe('Name')
    expect(headers[1].text()).toBe('Status')
    expect(headers[2].text()).toBe('Actions')
  })

  it('renders table rows', () => {
    const wrapper = mount(SbTable, {
      props: { columns, rows },
    })
    const trs = wrapper.findAll('tbody tr')
    expect(trs).toHaveLength(3)
    expect(trs[0].text()).toContain('Device A')
    expect(trs[0].text()).toContain('Online')
  })

  it('shows empty message when no rows', () => {
    const wrapper = mount(SbTable, {
      props: { columns, rows: [], emptyMessage: 'Nothing here' },
    })
    expect(wrapper.text()).toContain('Nothing here')
  })

  it('renders empty slot when provided', () => {
    const wrapper = mount(SbTable, {
      props: { columns, rows: [] },
      slots: { empty: 'Custom empty' },
    })
    expect(wrapper.text()).toContain('Custom empty')
  })

  it('applies column alignment', () => {
    const wrapper = mount(SbTable, {
      props: { columns, rows },
    })
    const actionsHeader = wrapper.findAll('th')[2]
    expect(actionsHeader.classes()).toContain('text-right')
  })

  describe('sorting', () => {
    it('marks sortable headers with aria-sort', () => {
      const wrapper = mount(SbTable, {
        props: { columns, rows, sortBy: 'name', sortDir: 'asc' as const },
      })
      const nameHeader = wrapper.findAll('th')[0]
      expect(nameHeader.attributes('aria-sort')).toBe('ascending')
    })

    it('shows aria-sort descending', () => {
      const wrapper = mount(SbTable, {
        props: { columns, rows, sortBy: 'name', sortDir: 'desc' as const },
      })
      const nameHeader = wrapper.findAll('th')[0]
      expect(nameHeader.attributes('aria-sort')).toBe('descending')
    })

    it('non-sorted sortable column has aria-sort none', () => {
      const wrapper = mount(SbTable, {
        props: { columns, rows, sortBy: 'status', sortDir: 'asc' as const },
      })
      const nameHeader = wrapper.findAll('th')[0]
      expect(nameHeader.attributes('aria-sort')).toBe('none')
    })

    it('non-sortable column has no aria-sort', () => {
      const wrapper = mount(SbTable, {
        props: { columns, rows },
      })
      const statusHeader = wrapper.findAll('th')[1]
      expect(statusHeader.attributes('aria-sort')).toBeUndefined()
    })

    it('emits sort events on sortable header click', async () => {
      const wrapper = mount(SbTable, {
        props: { columns, rows, sortBy: undefined, sortDir: 'asc' as const },
      })
      const nameHeader = wrapper.findAll('th')[0]
      await nameHeader.trigger('click')

      expect(wrapper.emitted('update:sortBy')).toBeTruthy()
      expect(wrapper.emitted('update:sortBy')![0]).toEqual(['name'])
      expect(wrapper.emitted('update:sortDir')).toBeTruthy()
      expect(wrapper.emitted('update:sortDir')![0]).toEqual(['asc'])
      expect(wrapper.emitted('sort')).toBeTruthy()
      expect(wrapper.emitted('sort')![0]).toEqual([{ key: 'name', dir: 'asc' }])
    })

    it('toggles direction when clicking same column', async () => {
      const wrapper = mount(SbTable, {
        props: { columns, rows, sortBy: 'name', sortDir: 'asc' as const },
      })
      const nameHeader = wrapper.findAll('th')[0]
      await nameHeader.trigger('click')

      expect(wrapper.emitted('update:sortDir')![0]).toEqual(['desc'])
    })

    it('does not emit sort on non-sortable header click', async () => {
      const wrapper = mount(SbTable, {
        props: { columns, rows },
      })
      const statusHeader = wrapper.findAll('th')[1]
      await statusHeader.trigger('click')

      expect(wrapper.emitted('sort')).toBeFalsy()
    })
  })

  describe('selectable', () => {
    it('shows checkboxes when selectable', () => {
      const wrapper = mount(SbTable, {
        props: { columns, rows, selectable: true, selectedRows: [] },
      })
      const checkboxes = wrapper.findAll('input[type="checkbox"]')
      // 1 header checkbox + 3 row checkboxes
      expect(checkboxes).toHaveLength(4)
    })

    it('header checkbox has select all label', () => {
      const wrapper = mount(SbTable, {
        props: { columns, rows, selectable: true, selectedRows: [] },
      })
      const headerCheckbox = wrapper.find('thead input[type="checkbox"]')
      expect(headerCheckbox.attributes('aria-label')).toBe('Select all rows')
    })

    it('emits update:selectedRows on row checkbox change', async () => {
      const wrapper = mount(SbTable, {
        props: { columns, rows, selectable: true, selectedRows: [] },
      })
      const rowCheckboxes = wrapper.findAll('tbody input[type="checkbox"]')
      await rowCheckboxes[0].trigger('change')

      expect(wrapper.emitted('update:selectedRows')).toBeTruthy()
      expect(wrapper.emitted('update:selectedRows')![0]).toEqual([['1']])
    })

    it('emits deselection on selected row checkbox', async () => {
      const wrapper = mount(SbTable, {
        props: { columns, rows, selectable: true, selectedRows: ['1', '2'] },
      })
      const rowCheckboxes = wrapper.findAll('tbody input[type="checkbox"]')
      await rowCheckboxes[0].trigger('change')

      expect(wrapper.emitted('update:selectedRows')![0]).toEqual([['2']])
    })

    it('select all emits all row ids', async () => {
      const wrapper = mount(SbTable, {
        props: { columns, rows, selectable: true, selectedRows: [] },
      })
      const headerCheckbox = wrapper.find('thead input[type="checkbox"]')
      await headerCheckbox.trigger('change')

      expect(wrapper.emitted('update:selectedRows')![0]).toEqual([
        ['1', '2', '3'],
      ])
    })

    it('deselect all when all selected', async () => {
      const wrapper = mount(SbTable, {
        props: {
          columns,
          rows,
          selectable: true,
          selectedRows: ['1', '2', '3'],
        },
      })
      const headerCheckbox = wrapper.find('thead input[type="checkbox"]')
      await headerCheckbox.trigger('change')

      expect(wrapper.emitted('update:selectedRows')![0]).toEqual([[]])
    })
  })

  it('emits rowClick on row click', async () => {
    const wrapper = mount(SbTable, {
      props: { columns, rows },
    })
    const trs = wrapper.findAll('tbody tr')
    await trs[0].trigger('click')

    expect(wrapper.emitted('rowClick')).toBeTruthy()
    expect(wrapper.emitted('rowClick')![0]).toEqual([rows[0]])
  })

  it('supports custom cell slots', () => {
    const wrapper = mount(SbTable, {
      props: { columns, rows },
      slots: {
        'cell-name': ({ value }: { value: unknown }) =>
          `Custom: ${value}`,
      },
    })
    expect(wrapper.text()).toContain('Custom: Device A')
  })

  it('uses custom rowKey', () => {
    const customRows = [
      { uid: 'x1', name: 'A', status: 'On' },
      { uid: 'x2', name: 'B', status: 'Off' },
    ]
    const wrapper = mount(SbTable, {
      props: {
        columns,
        rows: customRows,
        rowKey: 'uid',
        selectable: true,
        selectedRows: [],
      },
    })
    const rowCheckboxes = wrapper.findAll('tbody input[type="checkbox"]')
    expect(rowCheckboxes).toHaveLength(2)
  })
})
