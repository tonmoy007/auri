// Auri — useRetryCountdown hook
// Counts down the wait a failed request asked for, so the retry button can stay
// off until it is over. The arithmetic is in priestInput; this only ticks.

import { useEffect, useState } from 'react';
import { retryRemainingSeconds } from '../lib/priestInput';

const TICK_MS = 1000;

/** Whole seconds left of `retryAfterSeconds`, counted from `startedAtMs`; 0 when there is no wait. */
export function useRetryCountdown(startedAtMs: number, retryAfterSeconds: number | null): number {
  const [remaining, setRemaining] = useState(() =>
    retryRemainingSeconds(startedAtMs, retryAfterSeconds, Date.now()),
  );

  useEffect(() => {
    const update = (): number => {
      const left = retryRemainingSeconds(startedAtMs, retryAfterSeconds, Date.now());
      setRemaining(left);
      return left;
    };
    if (update() === 0) return undefined;
    const timer = setInterval(() => {
      if (update() === 0) clearInterval(timer);
    }, TICK_MS);
    return () => clearInterval(timer);
  }, [startedAtMs, retryAfterSeconds]);

  return remaining;
}
