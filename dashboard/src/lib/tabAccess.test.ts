import { describe, expect, it } from 'vitest'
import type { StaffUser, UserRole } from '@/lib/api'
import { TAB_ACCESS, visibleTabsFor } from '@/lib/tabAccess'

function userWith(role: UserRole): StaffUser {
  return { id: 'u1', email: `${role}@example.test`, role, is_active: true, last_login_at: null }
}

function labelsFor(user: StaffUser | null, legacy = false): string[] {
  return visibleTabsFor(user, legacy).map((tab) => tab.label)
}

describe('visibleTabsFor', () => {
  it('shows an HR user the people-side tabs and none of the developer tabs', () => {
    expect(labelsFor(userWith('hr'))).toEqual([
      'Insights',
      'Queue',
      'Directory',
      'Delivery',
      'Replies',
      'Themes',
      'Privacy',
    ])
  })

  it('shows a moderator the queue and nothing else', () => {
    expect(labelsFor(userWith('moderator'))).toEqual(['Queue'])
  })

  it('shows an administrator the admin tabs and the privacy page, not the HR-only tabs', () => {
    expect(labelsFor(userWith('admin'))).toEqual([
      'Privacy',
      'Config',
      'Status',
      'Build',
      'Guide',
      'Audit',
    ])
  })

  it('shows nobody signed in no tabs', () => {
    expect(labelsFor(null)).toEqual([])
  })

  it('shows the legacy admin key every tab, since it has no role', () => {
    expect(labelsFor(null, true)).toHaveLength(TAB_ACCESS.length)
  })

  it('keeps the developer tabs away from every role except admin', () => {
    const developerTabs = ['Config', 'Status', 'Build', 'Guide', 'Audit']
    for (const role of ['hr', 'moderator'] as const) {
      expect(labelsFor(userWith(role)).filter((label) => developerTabs.includes(label))).toEqual([])
    }
  })

  it('gives the Guide tab to administrators alone, never HR or a moderator', () => {
    const guide = TAB_ACCESS.find((tab) => tab.value === 'priest')

    expect(guide).toEqual({ value: 'priest', label: 'Guide', roles: ['admin'] })
    expect(labelsFor(userWith('hr'))).not.toContain('Guide')
    expect(labelsFor(userWith('moderator'))).not.toContain('Guide')
  })

  it('never lists a tab twice or with an empty role list', () => {
    const values = TAB_ACCESS.map((tab) => tab.value)
    expect(new Set(values).size).toBe(values.length)
    expect(TAB_ACCESS.every((tab) => tab.roles.length > 0)).toBe(true)
  })
})
