import { useEffect, useRef } from 'react';

interface PollingOptions {
  enabled?: boolean;
  immediate?: boolean;
  key?: unknown;
}

export function usePolling(
  task: (signal: AbortSignal) => Promise<void>,
  intervalMs: number,
  options: PollingOptions = {}
) {
  const taskRef = useRef(task);
  taskRef.current = task;
  const enabled = options.enabled ?? true;
  const immediate = options.immediate ?? true;

  useEffect(() => {
    if (!enabled) return;
    let running = false;
    let controller: AbortController | null = null;

    const run = async () => {
      if (running) return;
      running = true;
      controller = new AbortController();
      try {
        await taskRef.current(controller.signal);
      } catch (reason) {
        if (!(reason instanceof DOMException && reason.name === 'AbortError')) throw reason;
      } finally {
        running = false;
        controller = null;
      }
    };

    if (immediate) void run();
    const timer = window.setInterval(() => void run(), intervalMs);
    return () => {
      window.clearInterval(timer);
      controller?.abort();
    };
  }, [enabled, immediate, intervalMs, options.key]);
}
