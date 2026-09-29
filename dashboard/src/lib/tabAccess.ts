import type { StaffUser, UserRole } from '@/lib/api'

export interface TabAccess {
  value: string
  label: string
  roles: UserRole[]
}

/**
 * Which roles may see which tab.
 *
 * The Phase 10 developer tabs are admin-only: an HR lead has no business
 * rotating LLM API keys or building an APK, and a moderator less still. The
 * match is on the exact role, so an administrator sees the tabs that name
 * `admin` and not the HR-only ones. Later tabs register here as they land.
 */
export const TAB_ACCESS: TabAccess[] = [
  { value: 'insights', label: 'Insights', roles: ['hr'] },
  { value: 'queue', label: 'Queue', roles: ['hr', 'moderator'] },
  { value: 'directory', label: 'Directory', roles: ['hr'] },
  { value: 'delivery', label: 'Delivery', roles: ['hr'] },
  { value: 'replies', label: 'Replies', roles: ['hr'] },
  { value: 'themes', label: 'Themes', roles: ['hr'] },
  { value: 'privacy', label: 'Privacy', roles: ['hr', 'admin'] },
  { value: 'config', label: 'Config', roles: ['admin'] },
  { value: 'status', label: 'Status', roles: ['admin'] },
  { value: 'build', label: 'Build', roles: ['admin'] },
  { value: 'audit', label: 'Audit', roles: ['admin'] },
]

/**
 * The tabs a session may see.
 *
 * The legacy shared admin key has no role, so it sees every tab (it is an
 * escape hatch); a signed-in user sees the tabs naming their role; nobody
 * signed in sees none.
 */
export function visibleTabsFor(user: StaffUser | null, usingLegacyKey: boolean): TabAccess[] {
  return TAB_ACCESS.filter(
    (tab) => usingLegacyKey || (user !== null && tab.roles.includes(user.role)),
  )
}
