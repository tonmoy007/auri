// Auri — Anonymous device identity
// Shared by review.tsx (confession submission) and settings.tsx (identity reset).
// Single module-level cache so both screens see the same value within a session —
// two independent copies of this logic would silently diverge after a reset.

import * as Crypto from 'expo-crypto';
import * as SecureStore from 'expo-secure-store';

const DEVICE_TOKEN_STORAGE_KEY = 'auri_device_token';

let sessionDeviceToken: string | null = null;

/**
 * Return a stable device identifier that survives app restarts.
 *
 * Cached in-memory per process; backed by expo-secure-store so
 * device_token_hash (and confession ownership checks on delete/forward)
 * stays the same across app launches.
 */
export async function getSessionDeviceToken(): Promise<string> {
  if (sessionDeviceToken !== null) {
    return sessionDeviceToken;
  }

  const storedToken = await SecureStore.getItemAsync(DEVICE_TOKEN_STORAGE_KEY);
  if (storedToken !== null) {
    sessionDeviceToken = storedToken;
    return storedToken;
  }

  const newToken = Crypto.randomUUID();
  await SecureStore.setItemAsync(DEVICE_TOKEN_STORAGE_KEY, newToken);
  sessionDeviceToken = newToken;
  return newToken;
}

/** SHA-256 hex digest of the device token, matching the backend's device_token_hash contract. */
export async function hashDeviceToken(): Promise<string> {
  return Crypto.digestStringAsync(
    Crypto.CryptoDigestAlgorithm.SHA256,
    await getSessionDeviceToken(),
  );
}

/**
 * Discard the current anonymous identity and generate a new one.
 *
 * Irreversible: confessions submitted under the old token become
 * permanently inaccessible to this device (ownership is keyed on the
 * token hash, and the raw token itself is never sent to or stored by
 * the backend — there is no way to recover it after this call).
 *
 * Returns the new token's short reference (first 8 chars) for display.
 */
export async function resetDeviceToken(): Promise<string> {
  const newToken = Crypto.randomUUID();
  await SecureStore.setItemAsync(DEVICE_TOKEN_STORAGE_KEY, newToken);
  sessionDeviceToken = newToken;
  return newToken.slice(0, 8);
}

/** Short, non-sensitive reference to the current identity for display in Settings. */
export async function getDeviceIdentityReference(): Promise<string> {
  const token = await getSessionDeviceToken();
  return token.slice(0, 8);
}
