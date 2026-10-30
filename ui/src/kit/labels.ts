import { computed, inject, ref, type ComputedRef, type InjectionKey, type Ref } from 'vue'

/**
 * Words the kit components say themselves (close buttons, screen-reader names).
 * The kit does not depend on vue-i18n: the app provides translations once (App.vue);
 * without a provider the English defaults apply.
 */
export interface KitLabels {
  closeDialog: string
  closeDrawer: string
  dismissNotification: string
  removeBadge: string
  signalPath: string
  selectAllRows: string
  clearSearch: string
  loading: string
}

export const KIT_DEFAULT_LABELS: KitLabels = {
  closeDialog: 'Close dialog',
  closeDrawer: 'Close drawer',
  dismissNotification: 'Dismiss notification',
  removeBadge: 'Remove',
  signalPath: 'Signal path',
  selectAllRows: 'Select all rows',
  clearSearch: 'Clear search',
  loading: 'Loading',
}

export const KIT_LABELS_KEY: InjectionKey<Ref<KitLabels>> = Symbol('sb-kit-labels')

export function kitLabelsFrom(t: (key: string) => string): KitLabels {
  const out = {} as KitLabels
  for (const k of Object.keys(KIT_DEFAULT_LABELS) as (keyof KitLabels)[]) out[k] = t(`kit.${k}`)
  return out
}

export function useKitLabels(): ComputedRef<KitLabels> {
  const provided = inject(KIT_LABELS_KEY, ref(KIT_DEFAULT_LABELS))
  return computed(() => provided.value)
}
