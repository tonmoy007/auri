import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from 'react'
import { useAdminSettings } from '@/hooks/useAdminSettings'
import {
  ApiError,
  apiRequest,
  authApi,
  type Credentials,
  type StaffUser,
} from '@/lib/api'

interface AuthContextValue {
  /** Backend base URL, shared by the login screen and every panel. */
  baseUrl: string
  /** Legacy shared admin secret — an escape hatch, not the normal path. */
  adminKey: string
  updateConnection: (next: { baseUrl?: string; adminKey?: string }) => void
  user: StaffUser | null
  signedIn: boolean
  login: (email: string, password: string) => Promise<StaffUser>
  logout: () => Promise<void>
  /** Issue an authenticated request, refreshing the session once on 401. */
  authedRequest: <T>(path: string, init?: RequestInit) => Promise<T>
}

const AuthContext = createContext<AuthContextValue | null>(null)

/**
 * Staff session state for the dashboard.
 *
 * Tokens live in memory only — never localStorage, which any injected script
 * on the page can read. The cost is that a page reload signs you out; the
 * refresh token keeps a long working session alive without re-typing a
 * password, and it dies with the tab.
 */
export function AuthProvider({ children }: { children: ReactNode }) {
  const { baseUrl, adminKey, update } = useAdminSettings()
  const [user, setUser] = useState<StaffUser | null>(null)
  const accessToken = useRef<string | null>(null)
  const refreshToken = useRef<string | null>(null)

  const clearSession = useCallback(() => {
    accessToken.current = null
    refreshToken.current = null
    setUser(null)
  }, [])

  const credentials = useCallback(
    (): Credentials => ({ accessToken: accessToken.current, adminKey }),
    [adminKey],
  )

  const login = useCallback(
    async (email: string, password: string) => {
      const tokens = await authApi.login(baseUrl, email, password)
      accessToken.current = tokens.access_token
      refreshToken.current = tokens.refresh_token
      setUser(tokens.user)
      return tokens.user
    },
    [baseUrl],
  )

  const logout = useCallback(async () => {
    const token = accessToken.current
    clearSession()
    if (!token) return
    // Server-side revocation bumps the account's token_version; a failure
    // here (backend already unreachable) must not strand the user in a
    // signed-in UI, so the local session is cleared first either way.
    await authApi.logout(baseUrl, { accessToken: token }).catch(() => undefined)
  }, [baseUrl, clearSession])

  const renewSession = useCallback(async () => {
    if (!refreshToken.current) return false
    try {
      const tokens = await authApi.refresh(baseUrl, refreshToken.current)
      accessToken.current = tokens.access_token
      refreshToken.current = tokens.refresh_token
      setUser(tokens.user)
      return true
    } catch {
      clearSession()
      return false
    }
  }, [baseUrl, clearSession])

  const authedRequest = useCallback(
    async <T,>(path: string, init?: RequestInit): Promise<T> => {
      try {
        return await apiRequest<T>(baseUrl, path, credentials(), init)
      } catch (err) {
        const expired = err instanceof ApiError && err.status === 401
        if (!expired || !(await renewSession())) throw err
        return apiRequest<T>(baseUrl, path, credentials(), init)
      }
    },
    [baseUrl, credentials, renewSession],
  )

  const value = useMemo<AuthContextValue>(
    () => ({
      baseUrl,
      adminKey,
      updateConnection: update,
      user,
      signedIn: user !== null,
      login,
      logout,
      authedRequest,
    }),
    [baseUrl, adminKey, update, user, login, logout, authedRequest],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

/** Access the dashboard's session. Throws if used outside {@link AuthProvider}. */
export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext)
  if (context === null) throw new Error('useAuth must be used inside an AuthProvider')
  return context
}
