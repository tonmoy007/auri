// Auri — useBackendUrl hook
// Settings-screen state for the runtime backend URL override — lets one
// APK survive a changing ngrok URL without a rebuild (see config/api.ts).

import { useState, useEffect, useCallback } from 'react';
import * as SecureStore from 'expo-secure-store';
import {
  BACKEND_URL_STORAGE_KEY,
  getDefaultApiBaseUrl,
  setBackendUrlOverride,
} from '../config/api';

interface UseBackendUrlReturn {
  /** The runtime override, or `null` if none is set (using the build-time default). */
  override: string | null;
  /** The build-time default (`EXPO_PUBLIC_API_URL`), for display alongside the override. */
  defaultUrl: string;
  /** Whether the persisted override has finished loading from secure storage. */
  isLoaded: boolean;
  /** Validate, persist, and apply a new override. Returns `false` if `url` isn't a valid http(s) URL. */
  save: (url: string) => Promise<boolean>;
  /** Clear the override, reverting to the build-time default. */
  reset: () => Promise<void>;
}

function normalize(url: string): string | null {
  const trimmed = url.trim().replace(/\/+$/, '');
  if (!/^https?:\/\/.+/.test(trimmed)) return null;
  return trimmed;
}

/** Load and persist the Settings screen's backend URL override. */
export function useBackendUrl(): UseBackendUrlReturn {
  const [override, setOverride] = useState<string | null>(null);
  const [isLoaded, setIsLoaded] = useState(false);

  useEffect(() => {
    let cancelled = false;

    (async () => {
      const stored = await SecureStore.getItemAsync(BACKEND_URL_STORAGE_KEY);
      if (cancelled) return;
      setOverride(stored);
      setIsLoaded(true);
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  const save = useCallback(async (url: string) => {
    const normalized = normalize(url);
    if (!normalized) return false;
    await setBackendUrlOverride(normalized);
    setOverride(normalized);
    return true;
  }, []);

  const reset = useCallback(async () => {
    await setBackendUrlOverride(null);
    setOverride(null);
  }, []);

  return { override, defaultUrl: getDefaultApiBaseUrl(), isLoaded, save, reset };
}
