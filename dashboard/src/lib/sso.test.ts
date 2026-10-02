import { describe, expect, it } from 'vitest'
import { readSsoReturn, ssoErrorMessage, ssoStartUrl } from '@/lib/sso'

describe('readSsoReturn', () => {
  it('finds the one-time code the callback left', () => {
    expect(readSsoReturn('#oidc_code=abc-123')).toEqual({ kind: 'code', code: 'abc-123' })
  })

  it('finds a fixed error', () => {
    expect(readSsoReturn('#oidc_error=no_account')).toEqual({ kind: 'error', error: 'no_account' })
    expect(readSsoReturn('#oidc_error=cancelled')).toEqual({ kind: 'error', error: 'cancelled' })
  })

  it('treats an unknown error as a plain failure, never echoing it', () => {
    expect(readSsoReturn('#oidc_error=<script>')).toEqual({ kind: 'error', error: 'failed' })
  })

  it('is nothing on an ordinary visit', () => {
    expect(readSsoReturn('')).toBeNull()
    expect(readSsoReturn('#queue')).toBeNull()
  })
})

describe('ssoStartUrl', () => {
  it('points at the backend start route', () => {
    expect(ssoStartUrl('https://api.example/')).toBe('https://api.example/api/v1/auth/oidc/start')
  })
})

describe('ssoErrorMessage', () => {
  it('has a message for every error', () => {
    for (const error of ['failed', 'no_account', 'cancelled'] as const) {
      expect(ssoErrorMessage(error).length).toBeGreaterThan(0)
    }
  })
})
