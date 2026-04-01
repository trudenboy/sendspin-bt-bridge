<script setup lang="ts">
import { computed } from 'vue'
import { ArrowUp, ArrowDown } from 'lucide-vue-next'

interface Column {
  key: string
  label: string
  sortable?: boolean
  width?: string
  align?: 'left' | 'center' | 'right'
}

const props = withDefaults(
  defineProps<{
    columns: Column[]
    rows: Array<Record<string, unknown>>
    sortBy?: string
    sortDir?: 'asc' | 'desc'
    selectable?: boolean
    selectedRows?: string[]
    rowKey?: string
    emptyMessage?: string
  }>(),
  {
    sortBy: undefined,
    sortDir: 'asc',
    selectable: false,
    selectedRows: () => [],
    rowKey: 'id',
    emptyMessage: 'No data available',
  },
)

const emit = defineEmits<{
  'update:sortBy': [value: string]
  'update:sortDir': [value: 'asc' | 'desc']
  sort: [payload: { key: string; dir: 'asc' | 'desc' }]
  'update:selectedRows': [value: string[]]
  rowClick: [row: Record<string, unknown>]
}>()

const alignClass: Record<string, string> = {
  left: 'text-left',
  center: 'text-center',
  right: 'text-right',
}

const allSelected = computed(() => {
  if (props.rows.length === 0) return false
  return props.rows.every((row) =>
    props.selectedRows.includes(String(row[props.rowKey])),
  )
})

function getRowId(row: Record<string, unknown>): string {
  return String(row[props.rowKey])
}

function toggleSort(col: Column) {
  if (!col.sortable) return
  const newDir: 'asc' | 'desc' =
    props.sortBy === col.key && props.sortDir === 'asc' ? 'desc' : 'asc'
  emit('update:sortBy', col.key)
  emit('update:sortDir', newDir)
  emit('sort', { key: col.key, dir: newDir })
}

function ariaSortValue(
  col: Column,
): 'ascending' | 'descending' | 'none' | undefined {
  if (!col.sortable) return undefined
  if (props.sortBy !== col.key) return 'none'
  return props.sortDir === 'asc' ? 'ascending' : 'descending'
}

function toggleRow(rowId: string) {
  const current = [...props.selectedRows]
  const idx = current.indexOf(rowId)
  if (idx >= 0) {
    current.splice(idx, 1)
  } else {
    current.push(rowId)
  }
  emit('update:selectedRows', current)
}

function toggleAll() {
  if (allSelected.value) {
    emit('update:selectedRows', [])
  } else {
    emit(
      'update:selectedRows',
      props.rows.map((r) => getRowId(r)),
    )
  }
}
</script>

<template>
  <div class="overflow-x-auto">
    <table class="w-full border-collapse">
      <thead>
        <tr class="bg-surface-secondary/50 dark:bg-gray-800/50">
          <th
            v-if="selectable"
            class="px-4 py-3 text-left"
          >
            <input
              type="checkbox"
              :checked="allSelected"
              :indeterminate="selectedRows.length > 0 && !allSelected"
              aria-label="Select all rows"
              @change="toggleAll"
            />
          </th>
          <th
            v-for="col in columns"
            :key="col.key"
            class="px-4 py-3 text-sm uppercase tracking-wider text-text-secondary"
            :class="[
              alignClass[col.align || 'left'],
              col.sortable ? 'cursor-pointer select-none hover:underline' : '',
            ]"
            :style="col.width ? { width: col.width } : undefined"
            :aria-sort="ariaSortValue(col)"
            @click="toggleSort(col)"
          >
            <span class="inline-flex items-center gap-1">
              {{ col.label }}
              <template v-if="col.sortable && sortBy === col.key">
                <ArrowUp v-if="sortDir === 'asc'" class="h-3.5 w-3.5" />
                <ArrowDown v-else class="h-3.5 w-3.5" />
              </template>
            </span>
          </th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="row in rows"
          :key="getRowId(row)"
          class="border-b border-gray-100 transition-colors hover:bg-surface-secondary/30 dark:border-gray-700"
          :class="{ 'cursor-pointer': $attrs.onRowClick }"
          @click="emit('rowClick', row)"
        >
          <td v-if="selectable" class="px-4 py-3">
            <input
              type="checkbox"
              :checked="selectedRows.includes(getRowId(row))"
              :aria-label="`Select row ${getRowId(row)}`"
              @click.stop
              @change="toggleRow(getRowId(row))"
            />
          </td>
          <td
            v-for="col in columns"
            :key="col.key"
            class="px-4 py-3"
            :class="alignClass[col.align || 'left']"
          >
            <slot
              :name="`cell-${col.key}`"
              :row="row"
              :value="row[col.key]"
            >
              {{ row[col.key] }}
            </slot>
          </td>
        </tr>

        <!-- Empty state -->
        <tr v-if="rows.length === 0">
          <td
            :colspan="columns.length + (selectable ? 1 : 0)"
            class="px-4 py-12 text-center text-text-secondary"
          >
            <slot name="empty">
              {{ emptyMessage }}
            </slot>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
