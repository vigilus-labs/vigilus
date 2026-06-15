import { useEffect, useRef, useCallback } from 'react';
import type { WsEvent, WsEventType } from '@/types';

type EventCallback<T = unknown> = (event: WsEvent<T>) => void;
type EventMap = Partial<Record<WsEventType, EventCallback>>;

interface UseVigilusEventsOptions {
  /** Map of event types to callback handlers */
  events: EventMap;
  /** Whether to connect automatically (default: true) */
  enabled?: boolean;
}

const WS_RECONNECT_BASE_MS = 1000;
const WS_RECONNECT_MAX_MS = 30000;

/**
 * Hook that connects to the Vigilus WebSocket and dispatches
 * events to the provided callbacks. Reconnects with exponential backoff.
 */
export function useVigilusEvents({ events, enabled = true }: UseVigilusEventsOptions) {
  const wsRef = useRef<WebSocket | null>(null);
  const eventsRef = useRef(events);
  const retryCountRef = useRef(0);
  const retryTimerRef = useRef<ReturnType<typeof setTimeout>>();

  // Keep callbacks ref in sync without re-connecting
  eventsRef.current = events;

  const getWsUrl = useCallback(() => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${protocol}//${window.location.host}/ws`;
  }, []);

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    const ws = new WebSocket(getWsUrl());
    wsRef.current = ws;

    ws.onopen = () => {
      retryCountRef.current = 0;
    };

    ws.onmessage = (event) => {
      try {
        const data: WsEvent = JSON.parse(event.data);
        const handler = eventsRef.current[data.type];
        if (handler) {
          handler(data);
        }
      } catch {
        // ignore malformed messages
      }
    };

    ws.onclose = (event) => {
      wsRef.current = null;
      if (event.code === 4401) {
        window.dispatchEvent(new Event('vigilus:unauthorized'));
        return; // do not reconnect — session expired
      }
      scheduleReconnect();
    };

    ws.onerror = () => {
      ws.close();
    };
  }, [getWsUrl]);

  const scheduleReconnect = useCallback(() => {
    const delay = Math.min(
      WS_RECONNECT_BASE_MS * Math.pow(2, retryCountRef.current),
      WS_RECONNECT_MAX_MS,
    );
    retryCountRef.current += 1;
    retryTimerRef.current = setTimeout(() => {
      connect();
    }, delay);
  }, [connect]);

  useEffect(() => {
    if (!enabled) return;

    connect();

    return () => {
      clearTimeout(retryTimerRef.current);
      wsRef.current?.close();
      wsRef.current = null;
    };
  }, [enabled, connect]);
}
