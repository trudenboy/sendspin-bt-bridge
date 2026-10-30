import { ApiError } from '@/api/client'

export type ActionKind = 'volume' | 'mute' | 'release' | 'claim'

type Translate = (key: string, params?: Record<string, unknown>) => string

/** "Could not change the volume of Kitchen: <server reason>" for a failed speaker action. */
export function actionFailure(t: Translate, kind: ActionKind, name: string, error: unknown): string {
  const message = t(`device.actions.${kind}Failed`, { name })
  return error instanceof ApiError && error.message ? `${message}: ${error.message}` : message
}
