import { useEffect, useRef, useState } from "react";

interface PolledResult<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
}

export function usePolled<T>(
  loader: () => Promise<T>,
  intervalMs: number,
  deps: unknown[],
): PolledResult<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const loaderRef = useRef(loader);
  loaderRef.current = loader;

  useEffect(() => {
    let alive = true;
    setData(null);
    setError(null);
    setLoading(true);

    const run = async () => {
      try {
        const result = await loaderRef.current();
        if (!alive) return;
        setData(result);
        setError(null);
      } catch (caught) {
        if (!alive) return;
        setError(caught instanceof Error ? caught.message : "Request failed");
      } finally {
        if (alive) setLoading(false);
      }
    };

    void run();
    const timer = window.setInterval(run, intervalMs);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
    // deps are supplied by the caller to control when polling restarts
  }, deps);

  return { data, error, loading };
}
