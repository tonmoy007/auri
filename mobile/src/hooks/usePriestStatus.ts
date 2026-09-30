// Auri — usePriestStatus hook
// Reads GET /priest/status: whether the Guide is on, its name, and its limits.

import { useCallback, useEffect, useRef, useState } from 'react';
import { fetchPriestStatus, toPriestErrorInfo } from '../lib/priestApi';
import type { PriestErrorInfo, PriestStatus } from '../types/priest';

interface UsePriestStatusReturn {
  /** The latest status, or `null` while loading or after a failure. */
  status: PriestStatus | null;
  /** Why the last read failed; `null` when it succeeded or has not finished. */
  error: PriestErrorInfo | null;
  isLoading: boolean;
  /** Read the status again (e.g. when a screen regains focus, or on Retry). */
  reload: () => Promise<void>;
}

/**
 * Load the Guide's status once on mount. A failure leaves `status` null, which
 * callers treat as "the Guide is not available": the home entry point stays hidden.
 * A caller that reloads on focus anyway passes `false` to skip the duplicate first read.
 */
export function usePriestStatus(loadOnMount = true): UsePriestStatusReturn {
  const [status, setStatus] = useState<PriestStatus | null>(null);
  const [error, setError] = useState<PriestErrorInfo | null>(null);
  const [isLoading, setIsLoading] = useState(loadOnMount);
  const isMountedRef = useRef(true);

  const reload = useCallback(async () => {
    setIsLoading(true);
    try {
      const next = await fetchPriestStatus();
      if (!isMountedRef.current) return;
      setStatus(next);
      setError(null);
    } catch (failure: unknown) {
      if (!isMountedRef.current) return;
      setStatus(null);
      setError(toPriestErrorInfo(failure));
    } finally {
      if (isMountedRef.current) setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    isMountedRef.current = true;
    if (loadOnMount) void reload();
    return () => {
      isMountedRef.current = false;
    };
  }, [reload, loadOnMount]);

  return { status, error, isLoading, reload };
}
