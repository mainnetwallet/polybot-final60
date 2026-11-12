import { useEffect, useRef, useState } from "react";

interface LiveRefreshResult<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
}

/**
 * Loads data immediately and refreshes when the live websocket feed changes.
 */
export function useLiveRefresh<T>(
  loader: () => Promise<T>,
  deps: unknown[],
  refreshKey?: unknown,
  enabled = true,
): LiveRefreshResult<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const loaderRef = useRef(loader);
  loaderRef.current = loader;
  const runRef = useRef<() => void>(() => undefined);
  const firstKey = useRef(true);
  const requestId = useRef(0);

  useEffect(() => {
    if (!enabled) {
      setData(null);
      setError(null);
      setLoading(true);
      runRef.current = () => undefined;
      return;
    }

    let alive = true;
    setData(null);
    setError(null);
    setLoading(true);

    const run = async () => {
      const currentRequest = ++requestId.current;
      try {
        const result = await loaderRef.current();
        if (!alive || currentRequest !== requestId.current) return;
        setData(result);
        setError(null);
      } catch (caught) {
        if (!alive || currentRequest !== requestId.current) return;
        setError(caught instanceof Error ? caught.message : "Request failed");
      } finally {
        if (alive && currentRequest === requestId.current) setLoading(false);
      }
    };

    runRef.current = () => void run();
    void run();
    return () => {
      alive = false;
    };
    // deps are supplied by the caller to control when the data source changes
  }, [...deps, enabled]);

  useEffect(() => {
    if (firstKey.current) {
      firstKey.current = false;
      return;
    }
    runRef.current();
  }, [refreshKey]);

  return { data, error, loading };
}
