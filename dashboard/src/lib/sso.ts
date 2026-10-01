// Single sign-on helpers for the login screen (plan 15.10).
//
// The backend's callback sends the browser back here with either a one-time code
// (`#oidc_code=…`) or a fixed error (`#oidc_error=failed|no_account|cancelled`) in the
// URL fragment. A fragment never reaches a server or an access log, and the code is
// traded once for the session, so no session token is ever in a URL.

export type SsoErrorCode = 'failed' | 'no_account' | 'cancelled'

export type SsoReturn =
  | { kind: 'code'; code: string }
  | { kind: 'error'; error: SsoErrorCode }

const ERROR_CODES: readonly SsoErrorCode[] = ['failed', 'no_account', 'cancelled']

/** What the callback left in *hash* (`location.hash`), or null when nothing. */
export function readSsoReturn(hash: string): SsoReturn | null {
  const params = new URLSearchParams(hash.startsWith('#') ? hash.slice(1) : hash)
  const code = params.get('oidc_code')
  if (code) return { kind: 'code', code }
  const error = params.get('oidc_error')
  if (error === null) return null
  return {
    kind: 'error',
    error: (ERROR_CODES as readonly string[]).includes(error) ? (error as SsoErrorCode) : 'failed',
  }
}

/** Where the SSO button sends the browser. */
export function ssoStartUrl(baseUrl: string): string {
  return `${baseUrl.replace(/\/+$/, '')}/api/v1/auth/oidc/start`
}

/** The message for a failed sign-in. It never says whether a person exists at the provider. */
export function ssoErrorMessage(error: SsoErrorCode): string {
  switch (error) {
    case 'cancelled':
      return 'Single sign-on was cancelled.'
    case 'no_account':
      return 'Your sign-in worked, but it does not match an active Auri staff account. Ask an administrator.'
    case 'failed':
      return 'Single sign-on did not complete. Try again, or sign in with your password.'
  }
}
