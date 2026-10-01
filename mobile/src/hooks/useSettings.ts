// Auri — useSettings hook
// Persisted user preferences (default voice mask, default booth environment),
// backed by expo-secure-store so they survive app restarts.

import { useState, useEffect, useCallback, useRef } from 'react';
import * as SecureStore from 'expo-secure-store';
import type { VoiceMask, Environment } from '../types';
import type { TraditionId } from '../types/priest';
import {
  PRIEST_INTRO_ACK_KEY,
  introAckValue,
  isIntroAcknowledged,
  parseGuideTradition,
  parseShowGuideMode,
} from '../lib/priestInput';

const VOICE_MASK_STORAGE_KEY = 'auri_default_voice_mask';
const ENVIRONMENT_STORAGE_KEY = 'auri_default_environment';
const SHOW_GUIDE_MODE_STORAGE_KEY = 'auri_show_guide_mode';
const GUIDE_TRADITION_STORAGE_KEY = 'auri_guide_tradition';

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
  /** Whether the home screen offers the Guide (on by default). */
  showGuideMode: boolean;
  /** Optional tradition the Guide is limited to; `null` means all. */
  guideTradition: TraditionId | null;
  /** Update and persist whether the home screen offers the Guide. */
  setShowGuideMode: (show: boolean) => void;
  /** Update and persist the optional Guide tradition (`null` clears it). */
  setGuideTradition: (tradition: TraditionId | null) => void;
  /** Re-read stored values, e.g. when a screen regains focus after Settings changed them. */
  reload: () => Promise<void>;
}

/** Whether the user has accepted the current Guide disclaimer version. */
export async function hasAcknowledgedPriestIntro(disclaimerVersion: string): Promise<boolean> {
  const stored = await SecureStore.getItemAsync(PRIEST_INTRO_ACK_KEY);
  return isIntroAcknowledged(stored, disclaimerVersion);
}

/** Record that the user accepted this Guide disclaimer version. */
export async function acknowledgePriestIntro(disclaimerVersion: string): Promise<void> {
  await SecureStore.setItemAsync(PRIEST_INTRO_ACK_KEY, introAckValue(disclaimerVersion));
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
  const [showGuideMode, setShowGuideModeState] = useState(true);
  const [guideTradition, setGuideTraditionState] = useState<TraditionId | null>(null);
  const [isLoaded, setIsLoaded] = useState(false);
  const isMountedRef = useRef(true);

  const reload = useCallback(async () => {
    const [storedMask, storedEnvironment, storedShowGuide, storedTradition] =
      await Promise.all([
        SecureStore.getItemAsync(VOICE_MASK_STORAGE_KEY),
        SecureStore.getItemAsync(ENVIRONMENT_STORAGE_KEY),
        SecureStore.getItemAsync(SHOW_GUIDE_MODE_STORAGE_KEY),
        SecureStore.getItemAsync(GUIDE_TRADITION_STORAGE_KEY),
      ]);
    if (!isMountedRef.current) return;

    if (storedMask !== null && isVoiceMask(storedMask)) {
      setVoiceMaskState(storedMask);
    }
    if (storedEnvironment !== null && isEnvironment(storedEnvironment)) {
      setEnvironmentState(storedEnvironment);
    }
    setShowGuideModeState(parseShowGuideMode(storedShowGuide));
    setGuideTraditionState(parseGuideTradition(storedTradition));
    setIsLoaded(true);
  }, []);

  useEffect(() => {
    isMountedRef.current = true;
    void reload();
    return () => {
      isMountedRef.current = false;
    };
  }, [reload]);

  const setDefaultVoiceMask = useCallback((mask: VoiceMask) => {
    setVoiceMaskState(mask);
    void SecureStore.setItemAsync(VOICE_MASK_STORAGE_KEY, mask);
  }, []);

  const setDefaultEnvironment = useCallback((environment: Environment) => {
    setEnvironmentState(environment);
    void SecureStore.setItemAsync(ENVIRONMENT_STORAGE_KEY, environment);
  }, []);

  const setShowGuideMode = useCallback((show: boolean) => {
    setShowGuideModeState(show);
    void SecureStore.setItemAsync(SHOW_GUIDE_MODE_STORAGE_KEY, String(show));
  }, []);

  const setGuideTradition = useCallback((tradition: TraditionId | null) => {
    setGuideTraditionState(tradition);
    if (tradition) {
      void SecureStore.setItemAsync(GUIDE_TRADITION_STORAGE_KEY, tradition);
    } else {
      void SecureStore.deleteItemAsync(GUIDE_TRADITION_STORAGE_KEY);
    }
  }, []);

  return {
    defaultVoiceMask,
    defaultEnvironment,
    isLoaded,
    setDefaultVoiceMask,
    setDefaultEnvironment,
    showGuideMode,
    guideTradition,
    setShowGuideMode,
    setGuideTradition,
    reload,
  };
}
