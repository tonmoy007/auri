# ADR: Staff single sign-on (OpenID Connect)

- **Status:** accepted, 2026-10-01 (owner decision, plan task 15.10).

## Decision

Dashboard staff can sign in through **any standards-compliant OpenID Connect provider** (Microsoft Entra ID, Google Workspace, Okta, Keycloak, and so on). **Password login stays** for every account that has a password, as the fallback and for break-glass access. **MFA is the provider's job**: Auri does not add its own second factor.

## How it works

- **Flow.** The backend runs the authorization code flow with PKCE (S256), a random `state` and a `nonce`. The `state` is also kept in an HttpOnly, SameSite=Lax cookie scoped to `/api/v1/auth/oidc`, and it must match on the callback, so a sign-in started in one browser can't be finished in another (login CSRF).
- **Token checks.** The ID token's signature is checked against the provider's published keys, and only asymmetric algorithms are accepted (never `none`, never an HMAC keyed by the client secret). Issuer, audience, expiry and nonce are checked too, and `azp` when there are several audiences. The discovery document must name the configured issuer. Keys are cached for an hour, and an unknown key id triggers one refresh, which picks up key rotation.
- **Handover.** After the callback, the browser goes back to the dashboard with a **one-time code in the URL fragment** (fragments are never sent to a server or written to an access log). The dashboard trades the code once, within 60 seconds, for the same access and refresh tokens a password login issues. Session tokens never appear in a URL. The dashboard still keeps them in memory only.
- **Accounts.** SSO never creates an account; an admin still creates every one.
  - On the **first** SSO sign-in, the account is linked by the ID token's email, which must be marked `email_verified` (configurable).
  - Linking records `issuer|subject` in `users.oidc_subject`.
  - **From then on the account is found by that identity alone.** A later email change at the provider can't move the sign-in to someone else's account.
  - An account linked to one identity is never re-linked to another.
  - A deactivated account is refused.
- **Roles.** By default, the role is the one set in Auri. With `OIDC_ROLE_CLAIM` (for example `groups` or `roles`) and `OIDC_ROLE_MAP` (JSON: claim value → `admin`, `hr` or `moderator`), the provider decides the role at every sign-in:
  - When several values map, the most privileged role wins.
  - No mapped value means no sign-in.
  - A role change bumps `token_version`, which ends every session issued under the old role.
- **Audit.** Each SSO sign-in writes an `auth.login_oidc` audit row with the provider's host name, plus the role change when there is one. It never records the email or the subject. Session revocation (logout bumps `token_version`) is unchanged.
- **Errors.** Every failure returns the browser to the dashboard with one of three fixed codes: `failed`, `no_account` or `cancelled`. The log records the reason, never a name or an email.

## Setup

1. Register a web application with the provider. Set its redirect URI to `https://<backend>/api/v1/auth/oidc/callback` and its client authentication to `client_secret_basic`.
2. Set these in the backend environment (all of them are environment-only):
   - `OIDC_ISSUER`
   - `OIDC_CLIENT_ID`
   - `OIDC_CLIENT_SECRET`
   - `OIDC_REDIRECT_URI`
   - `OIDC_DASHBOARD_URL`
   - optionally `OIDC_PROVIDER_LABEL`, `OIDC_ROLE_CLAIM` and `OIDC_ROLE_MAP`

   SSO is off until the five required ones are set. They must use https, except a provider on `localhost` for local testing.
3. **Microsoft Entra ID** sends no `email_verified`. Either add the `email` optional claim and set `OIDC_REQUIRE_EMAIL_VERIFIED=false`, which is acceptable only when users can't set their own email in the tenant, or keep the default and use a provider that sends it.
4. Enforce MFA in the provider, at least for the people who hold the Auri `admin` role.

## Limits and alternatives

- **One process.** Sign-in attempts and handover codes live in memory, like the other short-lived stores (`stt_jobs`, the rate limiters). Several API processes would need a shared store or sticky sessions for SSO.
- **Handover login CSRF.** An attacker who completes their own sign-in could send a victim a dashboard link carrying the attacker's handover code. The victim would then be signed in as the attacker, inside a 60-second window. Binding the handover to the browser would need cross-origin cookies between the dashboard and the API. That was judged not worth it for a staff tool where the attacker must hold a staff account themselves.
- **Rejected: building our own TOTP second factor.** The provider already does it better, with recovery, device management and policy.
- **Rejected: a library such as Authlib.** The checks above are a small amount of code on PyJWT and `cryptography`, which are already dependencies, so no new package was needed.
- **Deferred:** SAML, which most providers can also offer as OIDC; SCIM provisioning; and making SSO mandatory (turning password login off per account).
