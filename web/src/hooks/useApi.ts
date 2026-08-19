import { useCallback, useEffect, useRef, useState } from "react";
import type { ApiError } from "../api/client";

export function useApi<T>(
  load: (signal: AbortSignal) => Promise<T>,
  dependencies: unknown[] = [],
) {
  const [data, setData] = useState<T>();
  const [error, setError] = useState<ApiError>();
  const [loading, setLoading] = useState(true);
  const [refreshKey, setRefreshKey] = useState(0);
  const quietRefresh = useRef(false);
  const refresh = useCallback(() => {
    setRefreshKey((value) => value + 1);
    setLoading(true);
    setError(undefined);
  }, []);
  const poll = useCallback(() => {
    quietRefresh.current = true;
    setRefreshKey((value) => value + 1);
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    const quiet = quietRefresh.current;
    quietRefresh.current = false;
    if (!quiet) queueMicrotask(() => {
      if (!controller.signal.aborted) setLoading(true);
    });
    load(controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) setData(value);
      })
      .catch((reason: ApiError) => {
        if (!controller.signal.aborted) setError(reason);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [refreshKey, ...dependencies]); // eslint-disable-line react-hooks/exhaustive-deps
  return { data, error, loading, refresh, poll };
}
