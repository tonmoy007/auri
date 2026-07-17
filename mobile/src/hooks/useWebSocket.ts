// Auri — useWebSocket hook
// WebSocket connection management with auto-reconnect

import { useState, useCallback, useRef, useEffect } from 'react';
import { WS_URL, WS_EVENTS } from '../config/api';
import type {
  WebSocketConnectionState,
  WebSocketMessage,
  TranscriptPartial,
  SummaryResult,
  MaskStatusUpdate,
  ErrorMessage,
} from '../types';

interface UseWebSocketOptions {
  /** Session ID for the WebSocket connection */
  sessionId: string;
  /** Whether to connect on mount */
  autoConnect?: boolean;
  /** Maximum number of reconnection attempts */
  maxReconnectAttempts?: number;
  /** Base delay between reconnection attempts (ms) */
  reconnectDelay?: number;
}

interface UseWebSocketReturn {
  /** Current connection state */
  connectionState: WebSocketConnectionState;
  /** Send a message over the WebSocket */
  send: (data: string | ArrayBuffer) => void;
  /** Manually connect */
  connect: () => void;
  /** Manually disconnect */
  disconnect: () => void;
  /** Latest partial transcript received */
  transcriptPartial: TranscriptPartial | null;
  /** Final transcript received */
  transcriptFinal: string | null;
  /** AI summary result */
  summary: SummaryResult | null;
  /** Mask processing status */
  maskStatus: MaskStatusUpdate | null;
  /** Last error message */
  error: ErrorMessage | null;
}

/**
 * WebSocket hook for real-time audio streaming.
 * Manages connection lifecycle, message parsing, and automatic reconnection.
 *
 * Features:
 * - Auto-connect on mount (configurable)
 * - Exponential backoff reconnection
 * - Message type dispatching
 * - Cleanup on unmount
 */
export function useWebSocket({
  sessionId,
  autoConnect = true,
  maxReconnectAttempts = 5,
  reconnectDelay = 1000,
}: UseWebSocketOptions): UseWebSocketReturn {
  const [connectionState, setConnectionState] =
    useState<WebSocketConnectionState>('disconnected');
  const [transcriptPartial, setTranscriptPartial] =
    useState<TranscriptPartial | null>(null);
  const [transcriptFinal, setTranscriptFinal] = useState<string | null>(null);
  const [summary, setSummary] = useState<SummaryResult | null>(null);
  const [maskStatus, setMaskStatus] = useState<MaskStatusUpdate | null>(null);
  const [error, setError] = useState<ErrorMessage | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const reconnectAttemptsRef = useRef(0);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const isMountedRef = useRef(true);

  /**
   * Handle incoming WebSocket messages.
   * Dispatches based on event type.
   */
  const handleMessage = useCallback((event: WebSocketMessageEvent) => {
    try {
      const message: WebSocketMessage = JSON.parse(event.data as string);
      const { event: eventType, payload } = message;

      switch (eventType) {
        case WS_EVENTS.TRANSCRIPT_PARTIAL: {
          const data = payload as unknown as TranscriptPartial;
          setTranscriptPartial(data);
          if (data.isFinal) {
            setTranscriptFinal(data.text);
          }
          break;
        }
        case WS_EVENTS.SUMMARY_READY: {
          const data = payload as unknown as SummaryResult;
          setSummary(data);
          break;
        }
        case WS_EVENTS.MASK_STATUS: {
          const data = payload as unknown as MaskStatusUpdate;
          setMaskStatus(data);
          break;
        }
        case WS_EVENTS.ERROR: {
          const data = payload as unknown as ErrorMessage;
          setError(data);
          break;
        }
        default:
          // Unknown event type — ignore
          break;
      }
    } catch (_parseError: unknown) {
      // If message isn't valid JSON, ignore it
    }
  }, []);

  /**
   * Establish the WebSocket connection.
   */
  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      return;
    }

    setConnectionState('connecting');
    setError(null);

    const wsUrl = `${WS_URL}?session_id=${sessionId}`;
    const ws = new WebSocket(wsUrl);

    ws.onopen = () => {
      if (!isMountedRef.current) {
        ws.close();
        return;
      }
      setConnectionState('connected');
      reconnectAttemptsRef.current = 0;
    };

    ws.onmessage = handleMessage;

    ws.onerror = () => {
      if (!isMountedRef.current) return;
      setError({
        code: 'CONNECTION_ERROR',
        message: 'WebSocket connection error',
      });
    };

    ws.onclose = () => {
      if (!isMountedRef.current) return;
      setConnectionState('disconnected');

      // Attempt reconnection
      if (reconnectAttemptsRef.current < maxReconnectAttempts) {
        setConnectionState('reconnecting');
        const delay =
          reconnectDelay * Math.pow(2, reconnectAttemptsRef.current);
        reconnectTimeoutRef.current = setTimeout(() => {
          reconnectAttemptsRef.current += 1;
          connect();
        }, delay);
      }
    };

    wsRef.current = ws;
  }, [sessionId, maxReconnectAttempts, reconnectDelay, handleMessage]);

  /**
   * Send data over the WebSocket.
   */
  const send = useCallback(
    (data: string | ArrayBuffer) => {
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        wsRef.current.send(data);
      } else {
        setError({
          code: 'NOT_CONNECTED',
          message: 'WebSocket is not connected',
        });
      }
    },
    [],
  );

  /**
   * Disconnect the WebSocket.
   */
  const disconnect = useCallback(() => {
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
    }
    reconnectAttemptsRef.current = maxReconnectAttempts; // Prevent reconnection
    wsRef.current?.close();
    wsRef.current = null;
    setConnectionState('disconnected');
  }, [maxReconnectAttempts]);

  // Connect on mount if autoConnect is enabled
  useEffect(() => {
    isMountedRef.current = true;

    if (autoConnect) {
      connect();
    }

    return () => {
      isMountedRef.current = false;
      disconnect();
    };
  }, [autoConnect, connect, disconnect]);

  return {
    connectionState,
    send,
    connect,
    disconnect,
    transcriptPartial,
    transcriptFinal,
    summary,
    maskStatus,
    error,
  };
}
