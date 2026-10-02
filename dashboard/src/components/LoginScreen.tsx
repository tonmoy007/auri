import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Separator } from '@/components/ui/separator'
import { useAuth } from '@/hooks/useAuth'
import { ApiError, authApi, type SsoStatus } from '@/lib/api'
import { readSsoReturn, ssoErrorMessage, ssoStartUrl } from '@/lib/sso'

const RATE_LIMITED = 429

/**
 * Sign-in for staff accounts, gating the whole dashboard: email and password, plus
 * single sign-on when the backend offers it (plan 15.10).
 */
export function LoginScreen() {
  const { baseUrl, updateConnection, login, completeSso } = useAuth()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  // Back from the provider: the callback left a one-time code or a fixed error in
  // the URL fragment. Read once, on first render.
  const [returned] = useState(() => readSsoReturn(window.location.hash))
  const [error, setError] = useState<string | null>(
    returned?.kind === 'error' ? ssoErrorMessage(returned.error) : null,
  )
  const [submitting, setSubmitting] = useState(returned?.kind === 'code')
  const [sso, setSso] = useState<SsoStatus | null>(null)
  // The code is single-use; React's development double effect must not spend it twice.
  const handledReturn = useRef(false)

  // The fragment is cleared first so a reload or a shared URL never carries it.
  useEffect(() => {
    if (returned === null || handledReturn.current) return
    handledReturn.current = true
    window.history.replaceState(null, '', window.location.pathname + window.location.search)
    if (returned.kind !== 'code') return
    completeSso(returned.code)
      .catch(() => setError(ssoErrorMessage('failed')))
      .finally(() => setSubmitting(false))
  }, [returned, completeSso])

  useEffect(() => {
    let cancelled = false
    authApi
      .ssoStatus(baseUrl)
      .then((status) => {
        if (!cancelled) setSso(status)
      })
      .catch(() => {
        // An older backend has no SSO route, or the URL is still being typed.
        if (!cancelled) setSso(null)
      })
    return () => {
      cancelled = true
    }
  }, [baseUrl])

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      await login(email, password)
    } catch (err) {
      setError(describeFailure(err))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center p-6">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>Sign in to Auri</CardTitle>
          <CardDescription>
            Staff accounts only. Ask an administrator to create one — there is no self-signup.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form className="grid gap-4" onSubmit={handleSubmit}>
            <div className="grid gap-1.5">
              <Label htmlFor="login-backend-url">Backend URL</Label>
              <Input
                id="login-backend-url"
                value={baseUrl}
                onChange={(e) => updateConnection({ baseUrl: e.target.value })}
                placeholder="http://localhost:8000"
              />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="login-email">Email</Label>
              <Input
                id="login-email"
                type="email"
                autoComplete="username"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
              />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="login-password">Password</Label>
              <Input
                id="login-password"
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
              />
            </div>

            {error && (
              <Alert variant="destructive">
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            )}

            <Button type="submit" disabled={submitting}>
              {submitting ? 'Signing in…' : 'Sign in'}
            </Button>
          </form>
          {sso?.enabled && (
            <div className="mt-4 grid gap-4">
              <Separator />
              <Button
                type="button"
                variant="outline"
                disabled={submitting}
                onClick={() => window.location.assign(ssoStartUrl(baseUrl))}
              >
                Sign in with {sso.label ?? 'single sign-on'}
              </Button>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

/**
 * Turn a failure into a message that helps without leaking whether the
 * account exists — the backend answers every bad credential identically,
 * and the UI must not undo that.
 */
function describeFailure(err: unknown): string {
  if (!(err instanceof ApiError)) return 'Could not reach the backend at that URL.'
  if (err.status === RATE_LIMITED) return 'Too many failed attempts. Wait a few minutes and try again.'
  return 'Those credentials were not accepted.'
}
