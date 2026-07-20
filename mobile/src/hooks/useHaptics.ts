// Auri — useHaptics hook
// Thin wrapper around expo-haptics — every call site names the moment
// (start recording, confirm delete, ...) rather than the raw feedback style,
// so the mapping from UX moment to feedback stays in one place.

import { useCallback } from 'react';
import * as Haptics from 'expo-haptics';

interface UseHapticsReturn {
  /** Recording started. */
  recordStart: () => void;
  /** Recording stopped successfully. */
  recordStop: () => void;
  /** A picker selection changed (voice mask, environment). */
  selectionChanged: () => void;
  /** An action completed successfully (confession sent, identity reset). */
  success: () => void;
  /** A destructive or cautionary action was confirmed (delete, reset). */
  warning: () => void;
}

/**
 * Haptic feedback for key moments in the confession flow and settings.
 *
 * Every method is fire-and-forget — haptics are a UX nicety, never
 * something a caller should await or let block/fail the action itself.
 */
export function useHaptics(): UseHapticsReturn {
  const recordStart = useCallback(() => {
    void Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
  }, []);

  const recordStop = useCallback(() => {
    void Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
  }, []);

  const selectionChanged = useCallback(() => {
    void Haptics.selectionAsync();
  }, []);

  const success = useCallback(() => {
    void Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
  }, []);

  const warning = useCallback(() => {
    void Haptics.notificationAsync(Haptics.NotificationFeedbackType.Warning);
  }, []);

  return { recordStart, recordStop, selectionChanged, success, warning };
}
