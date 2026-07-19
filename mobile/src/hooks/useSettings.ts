// Auri — useSettings hook
// Persisted user preferences (default voice mask, default booth environment),
// backed by expo-secure-store so they survive app restarts.

import { useState, useEffect, useCallback } from 'react';
import * as SecureStore from 'expo-secure-store';
import type { VoiceMask, Environment } from '../types';

const VOICE_MASK_STORAGE_KEY = 'auri_default_voice_mask';
const ENVIRONMENT_STORAGE_KEY = 'auri_default_environment';

const DEFAULT_VOICE_MASK: VoiceMask = 'ethereal';
const DEFAULT_ENVIRONMENT: Environment = 'classic';

const VALID_VOICE_MASKS: readonly VoiceMask[] = [
  'warm',
  'robotic',
  'ethereal',
  'deep',
  'random',
];
const VALID_ENVIRONMENTS: readonly Environment[] = ['classic', 'forest', 'rooftop'];

function isVoiceMask(value: string): value is VoiceMask {
  return (VALID_VOICE_MASKS as readonly string[]).includes(value);
}

function isEnvironment(value: string): value is Environment {
  return (VALID_ENVIRONMENTS as readonly string[]).includes(value);
}

interface UseSettingsReturn {
  /** Default voice mask applied when entering a new confession. */
  defaultVoiceMask: VoiceMask;
  /** Default booth environment applied when entering a new confession. */
  defaultEnvironment: Environment;
  /** Whether persisted values have finished loading from secure storage. */
  isLoaded: boolean;
  /** Update and persist the default voice mask. */
  setDefaultVoiceMask: (mask: VoiceMask) => void;
  /** Update and persist the default booth environment. */
  setDefaultEnvironment: (environment: Environment) => void;
}

/**
 * Load and persist the user's Settings-screen preferences.
 *
 * Values start at their hardcoded defaults and are replaced once the
 * secure-store read resolves (`isLoaded` flips to `true`) — callers that
 * need to seed one-time local state should wait for `isLoaded` rather than
 * reading the values synchronously on first render.
 */
export function useSettings(): UseSettingsReturn {
  const [defaultVoiceMask, setVoiceMaskState] =
    useState<VoiceMask>(DEFAULT_VOICE_MASK);
  const [defaultEnvironment, setEnvironmentState] =
    useState<Environment>(DEFAULT_ENVIRONMENT);
  const [isLoaded, setIsLoaded] = useState(false);

  useEffect(() => {
    let cancelled = false;

    (async () => {
      const [storedMask, storedEnvironment] = await Promise.all([
        SecureStore.getItemAsync(VOICE_MASK_STORAGE_KEY),
        SecureStore.getItemAsync(ENVIRONMENT_STORAGE_KEY),
      ]);
      if (cancelled) return;

      if (storedMask !== null && isVoiceMask(storedMask)) {
        setVoiceMaskState(storedMask);
      }
      if (storedEnvironment !== null && isEnvironment(storedEnvironment)) {
        setEnvironmentState(storedEnvironment);
      }
      setIsLoaded(true);
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  const setDefaultVoiceMask = useCallback((mask: VoiceMask) => {
    setVoiceMaskState(mask);
    void SecureStore.setItemAsync(VOICE_MASK_STORAGE_KEY, mask);
  }, []);

  const setDefaultEnvironment = useCallback((environment: Environment) => {
    setEnvironmentState(environment);
    void SecureStore.setItemAsync(ENVIRONMENT_STORAGE_KEY, environment);
  }, []);

  return {
    defaultVoiceMask,
    defaultEnvironment,
    isLoaded,
    setDefaultVoiceMask,
    setDefaultEnvironment,
  };
}
