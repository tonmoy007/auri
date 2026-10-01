import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { LoginScreen } from '@/components/LoginScreen'

const auth = vi.hoisted(() => ({ current: {} as Record<string, unknown> }))
vi.mock('@/hooks/useAuth', () => ({ useAuth: () => auth.current }))

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

describe('LoginScreen single sign-on', () => {
  const fetchMock = vi.fn()
  const completeSso = vi.fn()

  beforeEach(() => {
    fetchMock.mockReset()
    completeSso.mockReset()
    vi.stubGlobal('fetch', fetchMock)
    auth.current = {
      baseUrl: 'http://api.test',
      updateConnection: vi.fn(),
      login: vi.fn(),
      completeSso,
    }
    window.history.replaceState(null, '', '/')
  })
  afterEach(() => vi.unstubAllGlobals())

  it('offers the provider when the backend has SSO on', async () => {
    fetchMock.mockResolvedValue(json({ enabled: true, label: 'Company SSO' }))
    render(<LoginScreen />)
    expect(await screen.findByRole('button', { name: 'Sign in with Company SSO' })).toBeTruthy()
  })

  it('shows only the password form when SSO is off', async () => {
    fetchMock.mockResolvedValue(json({ enabled: false, label: null }))
    render(<LoginScreen />)
    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
    expect(screen.queryByRole('button', { name: /Sign in with/ })).toBeNull()
  })

  it('shows only the password form against a backend without SSO', async () => {
    fetchMock.mockResolvedValue(json({ detail: 'Not Found' }, 404))
    render(<LoginScreen />)
    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
    expect(screen.queryByRole('button', { name: /Sign in with/ })).toBeNull()
  })

  it('trades the code from the callback once and clears it from the URL', async () => {
    fetchMock.mockResolvedValue(json({ enabled: true, label: 'SSO' }))
    completeSso.mockResolvedValue({ id: 'u1' })
    window.history.replaceState(null, '', '/#oidc_code=one-time')

    render(<LoginScreen />)

    await waitFor(() => expect(completeSso).toHaveBeenCalledWith('one-time'))
    expect(completeSso).toHaveBeenCalledTimes(1)
    expect(window.location.hash).toBe('')
  })

  it('explains a sign-in with no matching account', async () => {
    fetchMock.mockResolvedValue(json({ enabled: true, label: 'SSO' }))
    window.history.replaceState(null, '', '/#oidc_error=no_account')

    render(<LoginScreen />)

    expect(await screen.findByText(/does not match an active Auri staff account/)).toBeTruthy()
    expect(window.location.hash).toBe('')
    expect(completeSso).not.toHaveBeenCalled()
  })

  it('says SSO did not complete when the code is refused', async () => {
    fetchMock.mockResolvedValue(json({ enabled: true, label: 'SSO' }))
    completeSso.mockRejectedValue(new Error('401'))
    window.history.replaceState(null, '', '/#oidc_code=stale')

    render(<LoginScreen />)

    expect(await screen.findByText(/Single sign-on did not complete/)).toBeTruthy()
  })

  it('keeps the password form working', async () => {
    fetchMock.mockResolvedValue(json({ enabled: true, label: 'SSO' }))
    const login = vi.fn().mockResolvedValue({ id: 'u1' })
    auth.current = { ...auth.current, login }
    render(<LoginScreen />)

    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'hr@example.test' } })
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'pw' } })
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))

    await waitFor(() => expect(login).toHaveBeenCalledWith('hr@example.test', 'pw'))
  })
})
