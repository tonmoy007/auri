import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import App from '@/App'
import type { StaffUser, UserRole } from '@/lib/api'

// The panels each fetch their own data and are covered by the backend; here only
// the shell's role gating is under test, so they are replaced with stubs.
vi.mock('@/components/AuditPanel', () => ({ AuditPanel: () => null }))
vi.mock('@/components/BuildPanel', () => ({ BuildPanel: () => null }))
vi.mock('@/components/ConfigTable', () => ({ ConfigTable: () => null }))
vi.mock('@/components/ConnectionBar', () => ({ ConnectionBar: () => null }))
vi.mock('@/components/DeliveryPanel', () => ({ DeliveryPanel: () => null }))
vi.mock('@/components/DirectoryPanel', () => ({ DirectoryPanel: () => null }))
vi.mock('@/components/InsightsPanel', () => ({ InsightsPanel: () => null }))
vi.mock('@/components/PrivacyPanel', () => ({ PrivacyPanel: () => null }))
vi.mock('@/components/QueuePanel', () => ({ QueuePanel: () => null }))
vi.mock('@/components/RepliesPanel', () => ({ RepliesPanel: () => null }))
vi.mock('@/components/StatusPanel', () => ({ StatusPanel: () => null }))
vi.mock('@/components/ThemesPanel', () => ({ ThemesPanel: () => null }))
vi.mock('@/components/LoginScreen', () => ({ LoginScreen: () => <p>login screen</p> }))

const auth = vi.hoisted(() => ({ current: {} as Record<string, unknown> }))
vi.mock('@/hooks/useAuth', () => ({ useAuth: () => auth.current }))

function signedInAs(role: UserRole): StaffUser {
  return { id: 'u1', email: `${role}@example.test`, role, is_active: true, last_login_at: null }
}

function setSession(user: StaffUser | null, adminKey = '') {
  auth.current = {
    baseUrl: 'http://api.test',
    adminKey,
    updateConnection: vi.fn(),
    user,
    signedIn: user !== null,
    logout: vi.fn(),
    authedRequest: vi.fn().mockResolvedValue({ llm: [], stt: [], voice_masks: [], build: [], analytics: [] }),
  }
}

function tabNames(): string[] {
  return screen.queryAllByRole('tab').map((tab) => tab.textContent ?? '')
}

describe('App role gating', () => {
  beforeEach(() => setSession(null))

  it('shows the sign-in screen and no tabs to a signed-out visitor', () => {
    render(<App />)

    expect(screen.getByText('login screen')).toBeTruthy()
    expect(tabNames()).toEqual([])
  })

  it('shows an HR user the HR tabs and no developer tabs', () => {
    setSession(signedInAs('hr'))
    render(<App />)

    expect(tabNames()).toEqual([
      'Insights',
      'Queue',
      'Directory',
      'Delivery',
      'Replies',
      'Themes',
      'Privacy',
    ])
  })

  it('shows a moderator only the queue', () => {
    setSession(signedInAs('moderator'))
    render(<App />)

    expect(tabNames()).toEqual(['Queue'])
  })

  it('shows an administrator the admin tabs', () => {
    setSession(signedInAs('admin'))
    render(<App />)

    expect(tabNames()).toEqual(['Privacy', 'Config', 'Status', 'Build', 'Audit'])
  })

  it('shows the legacy admin key every tab', () => {
    setSession(null, 'legacy-key')
    render(<App />)

    expect(tabNames()).toHaveLength(11)
  })

  it('says who is signed in and with what role', () => {
    setSession(signedInAs('hr'))
    render(<App />)

    expect(screen.getByText('Signed in as hr@example.test (hr)')).toBeTruthy()
  })
})
