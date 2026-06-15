/**
 * SSE client for streaming orchestrator activity events.
 *
 * Usage:
 *   const stream = new ChatStream(sessionId);
 *   stream.on('delegation_start', (data) => { ... });
 *   stream.on('done', () => { stream.close(); });
 *   stream.start();
 */

export type SSEEventType =
  | 'thinking'
  | 'delegation_start'
  | 'tool_call'
  | 'tool_result'
  | 'delegation_result'
  | 'text_delta'
  | 'jit_request'
  | 'done'
  | 'error';

export interface SSEEventData {
  // thinking
  iteration?: number;
  // delegation_start
  operator?: string;
  task?: string;
  // tool_call / tool_result
  tool?: string;
  success?: boolean;
  preview?: string;
  // web_search / web_fetch (research)
  query?: string;
  url?: string;
  // delegation_result
  status?: string;
  summary?: string;
  // text_delta
  text?: string;
  // done
  message_id?: string;
  session_id?: string;
  // error
  error?: string;
  // jit_request
  id?: string;
  operator_name?: string;
  resource?: string;
  permission?: string;
  task_description?: string;
}

type EventHandler = (data: SSEEventData) => void;

export class ChatStream {
  private sessionId: string;
  private listeners: Map<string, Set<EventHandler>> = new Map();
  private eventSource: EventSource | null = null;
  private baseUrl: string;

  constructor(sessionId: string, baseUrl = '/api') {
    this.sessionId = sessionId;
    this.baseUrl = baseUrl;
  }

  /** Register an event handler. */
  on(event: SSEEventType, handler: EventHandler): void {
    if (!this.listeners.has(event)) {
      this.listeners.set(event, new Set());
    }
    this.listeners.get(event)!.add(handler);
  }

  /** Remove an event handler. */
  off(event: SSEEventType, handler: EventHandler): void {
    this.listeners.get(event)?.delete(handler);
  }

  /** Open the SSE connection and start receiving events. */
  start(): void {
    if (this.eventSource) {
      this.close();
    }

    const url = `${this.baseUrl}/sessions/${this.sessionId}/stream`;
    this.eventSource = new EventSource(url);

    const eventTypes: SSEEventType[] = [
      'thinking',
      'delegation_start',
      'tool_call',
      'tool_result',
      'delegation_result',
      'text_delta',
      'jit_request',
      'done',
      'error',
    ];

    for (const type of eventTypes) {
      this.eventSource.addEventListener(type, (e: MessageEvent) => {
        try {
          const data = JSON.parse(e.data) as SSEEventData;
          this.emit(type, data);
        } catch {
          // ignore malformed data
        }
      });
    }

    this.eventSource.onerror = () => {
      // SSE connection closed or errored — auto-reconnect is handled by EventSource,
      // but we emit done so the UI can update.
      // Only emit if we haven't already received a done event.
    };
  }

  /** Close the SSE connection. */
  close(): void {
    if (this.eventSource) {
      this.eventSource.close();
      this.eventSource = null;
    }
  }

  private emit(event: SSEEventType, data: SSEEventData): void {
    const handlers = this.listeners.get(event);
    if (handlers) {
      for (const handler of handlers) {
        try {
          handler(data);
        } catch (err) {
          console.error(`SSE handler error for ${event}:`, err);
        }
      }
    }
  }
}

/**
 * Activity event for the UI's live feed.
 */
export interface ActivityEvent {
  id: string;
  type: SSEEventType;
  data: SSEEventData;
  timestamp: number;
}

/** Generate a simple unique ID for activity events. */
let _actCounter = 0;
export function nextActivityId(): string {
  return `act-${Date.now()}-${++_actCounter}`;
}
