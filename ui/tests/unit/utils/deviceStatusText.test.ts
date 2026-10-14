import { describe, it, expect } from 'vitest'
import { releaseKind, standbyElapsedMinutes } from '@/utils/deviceStatusText'
import { makeDevice } from '../../fixtures/device'

const NOW = Date.parse('2026-10-14T10:00:00Z')

describe('standbyElapsedMinutes', () => {
  const since = (iso: string | null, standby = true) => makeDevice({ bluetooth: { standby, standby_since: iso } })

  it('counts whole minutes since the speaker entered standby', () => {
    expect(standbyElapsedMinutes(since('2026-10-14T09:48:00Z'), NOW)).toBe(12)
    expect(standbyElapsedMinutes(since('2026-10-14T09:59:31Z'), NOW)).toBe(0)
    expect(standbyElapsedMinutes(since('2026-10-14T08:45:00Z'), NOW)).toBe(75)
  })

  it('is null without standby, without a time, with a bad time or a future time, and for an output', () => {
    expect(standbyElapsedMinutes(since('2026-10-14T09:48:00Z', false), NOW)).toBeNull()
    expect(standbyElapsedMinutes(since(null), NOW)).toBeNull()
    expect(standbyElapsedMinutes(since('soon'), NOW)).toBeNull()
    expect(standbyElapsedMinutes(since('2026-10-14T10:05:00Z'), NOW)).toBeNull()
    expect(standbyElapsedMinutes(makeDevice({ bluetooth: null } as never), NOW)).toBeNull()
  })
})

describe('releaseKind', () => {
  it('tells an automatic release from a user one', () => {
    expect(releaseKind(makeDevice({ bluetooth: { management_enabled: false, released_by: 'auto' } }))).toBe('auto')
    expect(releaseKind(makeDevice({ bluetooth: { management_enabled: false, released_by: 'user' } }))).toBe('user')
    expect(releaseKind(makeDevice({ bluetooth: { management_enabled: false, released_by: null } }))).toBe('user')
  })

  it('is null while the bridge manages the speaker, and for an output', () => {
    expect(releaseKind(makeDevice({ bluetooth: { management_enabled: true, released_by: 'auto' } }))).toBeNull()
    expect(releaseKind(makeDevice({ bluetooth: null } as never))).toBeNull()
  })
})
