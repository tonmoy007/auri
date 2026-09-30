// Auri — usePriestConversation hook
// The Guide conversation, held in React state only. Nothing here is written to
// storage, logged or sent anywhere but the ask request: leaving the screen or
// tapping Clear discards every question and answer.

import { useCallback, useEffect, useRef, useState } from 'react';
import { AccessibilityInfo } from 'react-native';
import { askPriest, toPriestErrorInfo } from '../lib/priestApi';
import { canSubmitQuestion } from '../lib/priestInput';
import {
  STAGE_SWITCH_MS,
  errorAnnouncement,
  presentAnswer,
  presentError,
  type AnswerPresentation,
  type ErrorPresentation,
  type PendingStage,
} from '../lib/priestPresentation';
import type { TraditionId } from '../types/priest';

export type ChatMessage =
  | { id: string; role: 'user'; text: string }
  | { id: string; role: 'guide'; presentation: AnswerPresentation };

interface UsePriestConversationReturn {
  messages: ChatMessage[];
  draft: string;
  setDraft: (text: string) => void;
  isPending: boolean;
  stage: PendingStage;
  error: ErrorPresentation | null;
  /** When the current error arrived (ms since epoch), for counting down a retry wait. */
  errorAt: number;
  /** Bumped by every Clear, so the composer can drop a recording or transcript in flight. */
  clearCount: number;
  send: () => void;
  cancel: () => void;
  clear: () => void;
}

/**
 * Drive one Guide conversation.
 *
 * The question stays in the composer until an answer arrives, so a failure or a
 * Cancel leaves it there to edit or resend; the question bubble added while
 * waiting is taken back out then, so it is not shown twice.
 */
export function usePriestConversation(
  tradition: TraditionId | null,
  maxChars: number,
): UsePriestConversationReturn {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState('');
  const [isPending, setIsPending] = useState(false);
  const [stage, setStage] = useState<PendingStage>('searching');
  const [error, setError] = useState<ErrorPresentation | null>(null);
  const [errorAt, setErrorAt] = useState(0);
  const [clearCount, setClearCount] = useState(0);
  const abortRef = useRef<AbortController | null>(null);
  // Set the moment a send starts, before React re-renders with isPending, so two
  // presses in the same frame cannot both send.
  const isSendingRef = useRef(false);
  const nextIdRef = useRef(0);
  const isMountedRef = useRef(true);

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
      abortRef.current?.abort();
    };
  }, []);

  // The two status lines are client-side timed, not server progress.
  useEffect(() => {
    if (!isPending) return undefined;
    setStage('searching');
    const timer = setTimeout(() => setStage('reflecting'), STAGE_SWITCH_MS);
    return () => clearTimeout(timer);
  }, [isPending]);

  const send = useCallback(() => {
    const question = draft.trim();
    if (isSendingRef.current || isPending || !canSubmitQuestion(question, maxChars)) return;
    isSendingRef.current = true;
    const controller = new AbortController();
    abortRef.current = controller;
    const userId = `u${nextIdRef.current++}`;
    setMessages((prev) => [...prev, { id: userId, role: 'user', text: question }]);
    setError(null);
    setIsPending(true);

    askPriest({ question, tradition }, { signal: controller.signal })
      .then((answer) => {
        if (!isMountedRef.current) return;
        const presentation = presentAnswer(answer);
        const guideId = `g${nextIdRef.current++}`;
        setMessages((prev) => [...prev, { id: guideId, role: 'guide', presentation }]);
        setDraft('');
        AccessibilityInfo.announceForAccessibility(presentation.announcement);
      })
      .catch((failure: unknown) => {
        if (!isMountedRef.current) return;
        setMessages((prev) => prev.filter((m) => m.id !== userId));
        const presentation = presentError(toPriestErrorInfo(failure));
        setError(presentation);
        setErrorAt(Date.now());
        // Only a successful answer was spoken before; a failure has to be heard too.
        AccessibilityInfo.announceForAccessibility(errorAnnouncement(presentation));
      })
      .finally(() => {
        isSendingRef.current = false;
        if (abortRef.current === controller) abortRef.current = null;
        if (isMountedRef.current) setIsPending(false);
      });
  }, [draft, isPending, maxChars, tradition]);

  const cancel = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const clear = useCallback(() => {
    abortRef.current?.abort();
    setMessages([]);
    setDraft('');
    setError(null);
    setClearCount((count) => count + 1);
  }, []);

  return {
    messages,
    draft,
    setDraft,
    isPending,
    stage,
    error,
    errorAt,
    clearCount,
    send,
    cancel,
    clear,
  };
}
