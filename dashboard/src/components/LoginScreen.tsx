import { useState, type FormEvent } from 'react'
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
import { useAuth } from '@/hooks/useAuth'
import { ApiError } from '@/lib/api'

const RATE_LIMITED = 429

/** Email + password sign-in for staff accounts, gating the whole dashboard. */
export function LoginScreen() {
  const { baseUrl, updateConnection, login } = useAuth()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

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
