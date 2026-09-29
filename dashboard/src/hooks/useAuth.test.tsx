import { act, renderHook } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AuthProvider, useAuth } from '@/hooks/useAuth'
import { ApiError, type StaffUser, type TokenPair } from '@/lib/api'

const USER: StaffUser = {
  id: 'u1',
  email: 'hr@example.test',
  role: 'hr',
  is_active: true,
  last_login_at: null,
}

function pair(access: string, refresh: string): TokenPair {
  return { access_token: access, refresh_token: refresh, token_type: 'bearer', expires_in: 900, user: USER }
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function wrapper({ children }: { children: ReactNode }) {
  return <AuthProvider>{children}</AuthProvider>
}

function callsTo(fetchMock: ReturnType<typeof vi.fn>, suffix: string) {
  return fetchMock.mock.calls.filter(([url]) => String(url).endsWith(suffix))
}

function authHeaderOf(call: unknown[]): string | undefined {
  const init = call[1] as RequestInit | undefined
  return (init?.headers as Record<string, string> | undefined)?.Authorization
}

describe('AuthProvider', () => {
  const fetchMock = vi.fn()

  beforeEach(() => {
    fetchMock.mockReset()
    vi.stubGlobal('fetch', fetchMock)
  })
  afterEach(() => vi.unstubAllGlobals())

  it('throws if used outside a provider', () => {
    expect(() => renderHook(() => useAuth())).toThrow('inside an AuthProvider')
  })

  it('starts signed out', () => {
    const { result } = renderHook(() => useAuth(), { wrapper })
    expect(result.current.signedIn).toBe(false)
    expect(result.current.user).toBeNull()
  })

  it('signs in and exposes the user', async () => {
    fetchMock.mockResolvedValueOnce(json(pair('access-1', 'refresh-1')))
    const { result } = renderHook(() => useAuth(), { wrapper })

    await act(async () => {
      await result.current.login('hr@example.test', 'a-long-enough-password')
    })

    expect(result.current.signedIn).toBe(true)
    expect(result.current.user?.role).toBe('hr')
  })

  it('never writes a token to browser storage', async () => {
    fetchMock.mockResolvedValueOnce(json(pair('access-secret-1', 'refresh-secret-1')))
    const { result } = renderHook(() => useAuth(), { wrapper })

    await act(async () => {
      await result.current.login('hr@example.test', 'a-long-enough-password')
    })

    const stored = JSON.stringify({ ...localStorage }) + JSON.stringify({ ...sessionStorage })
    expect(stored).not.toContain('access-secret-1')
    expect(stored).not.toContain('refresh-secret-1')
  })

  it('sends the access token on authenticated requests', async () => {
    fetchMock.mockResolvedValueOnce(json(pair('access-1', 'refresh-1')))
    fetchMock.mockResolvedValueOnce(json({ ok: true }))
    const { result } = renderHook(() => useAuth(), { wrapper })
    await act(async () => {
      await result.current.login('hr@example.test', 'a-long-enough-password')
    })

    await act(async () => {
      await result.current.authedRequest('/hr/insights')
    })

    expect(authHeaderOf(callsTo(fetchMock, '/hr/insights')[0])).toBe('Bearer access-1')
  })

  it('refreshes once on a 401 and retries with the new token', async () => {
    fetchMock.mockResolvedValueOnce(json(pair('access-1', 'refresh-1')))
    fetchMock.mockResolvedValueOnce(json({ detail: 'expired' }, 401))
    fetchMock.mockResolvedValueOnce(json(pair('access-2', 'refresh-2')))
    fetchMock.mockResolvedValueOnce(json({ ok: true }))
    const { result } = renderHook(() => useAuth(), { wrapper })
    await act(async () => {
      await result.current.login('hr@example.test', 'a-long-enough-password')
    })

    let reply: unknown
    await act(async () => {
      reply = await result.current.authedRequest('/hr/insights')
    })

    expect(reply).toEqual({ ok: true })
    expect(callsTo(fetchMock, '/auth/refresh')).toHaveLength(1)
    const attempts = callsTo(fetchMock, '/hr/insights')
    expect(attempts.map(authHeaderOf)).toEqual(['Bearer access-1', 'Bearer access-2'])
  })

  it('does not loop: a second 401 after refreshing is thrown to the caller', async () => {
    fetchMock.mockResolvedValueOnce(json(pair('access-1', 'refresh-1')))
    fetchMock.mockResolvedValueOnce(json({ detail: 'expired' }, 401))
    fetchMock.mockResolvedValueOnce(json(pair('access-2', 'refresh-2')))
    fetchMock.mockResolvedValueOnce(json({ detail: 'still no' }, 401))
    const { result } = renderHook(() => useAuth(), { wrapper })
    await act(async () => {
      await result.current.login('hr@example.test', 'a-long-enough-password')
    })

    await act(async () => {
      await expect(result.current.authedRequest('/hr/insights')).rejects.toBeInstanceOf(ApiError)
    })

    expect(callsTo(fetchMock, '/auth/refresh')).toHaveLength(1)
  })

  it('clears the session when the refresh itself is refused', async () => {
    fetchMock.mockResolvedValueOnce(json(pair('access-1', 'refresh-1')))
    fetchMock.mockResolvedValueOnce(json({ detail: 'expired' }, 401))
    fetchMock.mockResolvedValueOnce(json({ detail: 'revoked' }, 401))
    const { result } = renderHook(() => useAuth(), { wrapper })
    await act(async () => {
      await result.current.login('hr@example.test', 'a-long-enough-password')
    })

    await act(async () => {
      await expect(result.current.authedRequest('/hr/insights')).rejects.toMatchObject({ status: 401 })
    })

    expect(result.current.signedIn).toBe(false)
  })

  it('does not refresh for an error that is not a 401', async () => {
    fetchMock.mockResolvedValueOnce(json(pair('access-1', 'refresh-1')))
    fetchMock.mockResolvedValueOnce(json({ detail: 'nope' }, 403))
    const { result } = renderHook(() => useAuth(), { wrapper })
    await act(async () => {
      await result.current.login('hr@example.test', 'a-long-enough-password')
    })

    await act(async () => {
      await expect(result.current.authedRequest('/hr/insights')).rejects.toMatchObject({ status: 403 })
    })

    expect(callsTo(fetchMock, '/auth/refresh')).toHaveLength(0)
    expect(result.current.signedIn).toBe(true)
  })

  it('signs out locally first and revokes the session on the server', async () => {
    fetchMock.mockResolvedValueOnce(json(pair('access-1', 'refresh-1')))
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }))
    const { result } = renderHook(() => useAuth(), { wrapper })
    await act(async () => {
      await result.current.login('hr@example.test', 'a-long-enough-password')
    })

    await act(async () => {
      await result.current.logout()
    })

    expect(result.current.signedIn).toBe(false)
    expect(authHeaderOf(callsTo(fetchMock, '/auth/logout')[0])).toBe('Bearer access-1')
  })

  it('stays signed out even if the server cannot be reached to log out', async () => {
    fetchMock.mockResolvedValueOnce(json(pair('access-1', 'refresh-1')))
    fetchMock.mockRejectedValueOnce(new TypeError('network down'))
    const { result } = renderHook(() => useAuth(), { wrapper })
    await act(async () => {
      await result.current.login('hr@example.test', 'a-long-enough-password')
    })

    await act(async () => {
      await result.current.logout()
    })

    expect(result.current.signedIn).toBe(false)
  })

  it('makes no logout request when nobody is signed in', async () => {
    const { result } = renderHook(() => useAuth(), { wrapper })

    await act(async () => {
      await result.current.logout()
    })

    expect(fetchMock).not.toHaveBeenCalled()
  })
})
