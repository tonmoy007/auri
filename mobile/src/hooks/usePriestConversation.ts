// Auri — usePriestConversation hook
// The Guide conversation, held in React state only. Nothing here is written to
// storage, logged or sent anywhere but the ask request: leaving the screen or
// tapping Clear discards every question and answer.

import { useCallback, useEffect, useRef, useState } from 'react';
import { AccessibilityInfo } from 'react-native';
import { askPriest, toPriestErrorInfo } from '../lib/priestApi';
import {
  STAGE_SWITCH_MS,
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
export function usePriestConversation(tradition: TraditionId | null): UsePriestConversationReturn {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState('');
  const [isPending, setIsPending] = useState(false);
  const [stage, setStage] = useState<PendingStage>('searching');
  const [error, setError] = useState<ErrorPresentation | null>(null);
  const abortRef = useRef<AbortController | null>(null);
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
    if (isPending || !question) return;
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
        setError(presentError(toPriestErrorInfo(failure)));
      })
      .finally(() => {
        if (abortRef.current === controller) abortRef.current = null;
        if (isMountedRef.current) setIsPending(false);
      });
  }, [draft, isPending, tradition]);

  const cancel = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const clear = useCallback(() => {
    abortRef.current?.abort();
    setMessages([]);
    setDraft('');
    setError(null);
  }, []);

  return { messages, draft, setDraft, isPending, stage, error, send, cancel, clear };
}
