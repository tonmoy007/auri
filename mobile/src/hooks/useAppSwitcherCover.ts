// Auri — useAppSwitcherCover hook (plan task 16.6)

import { useEffect, useState } from 'react';
import { AppState } from 'react-native';
import { shouldCoverScreen } from '../lib/appSwitcherCover';

/** `true` while the app is not active, so the screen can hide what is on it. */
export function useAppSwitcherCover(): boolean {
  // The screen is opened from the app, so it starts active; reading the current
  // state still covers the rare mount while the app is on its way out.
  const [covered, setCovered] = useState(() => shouldCoverScreen(AppState.currentState));
  useEffect(() => {
    const subscription = AppState.addEventListener('change', (next) => {
      setCovered(shouldCoverScreen(next));
    });
    return () => subscription.remove();
  }, []);
  return covered;
}
