// Auri — useReducedMotion hook
// Tracks the OS "reduce motion" setting so animated states can switch off.

import { useEffect, useState } from 'react';
import { AccessibilityInfo } from 'react-native';

/** Whether the user has asked the OS to reduce motion; updates if they change it. */
export function useReducedMotion(): boolean {
  const [reduceMotion, setReduceMotion] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void AccessibilityInfo.isReduceMotionEnabled().then((enabled) => {
      if (!cancelled) setReduceMotion(enabled);
    });
    const subscription = AccessibilityInfo.addEventListener('reduceMotionChanged', setReduceMotion);
    return () => {
      cancelled = true;
      subscription.remove();
    };
  }, []);

  return reduceMotion;
}
