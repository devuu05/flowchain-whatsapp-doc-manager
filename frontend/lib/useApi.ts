"use client";

import { useCallback, useEffect, useState } from "react";

import { ApiError, api } from "./api";

export interface ApiState<T> {
  data: T | undefined;
  error: ApiError | Error | undefined;
  loading: boolean;
  reload: () => void;
}

interface Result<T> {
  key: string | null;
  data?: T;
  error?: ApiError | Error;
}

/** Fetch `path` (relative to /api/v1) and expose loading/error/data. `null` path = skip.
 *  Previous data is kept while a reload is in flight so screens don't flash empty. */
export function useApi<T>(path: string | null): ApiState<T> {
  const [tick, setTick] = useState(0);
  const key = path === null ? null : `${path}#${tick}`;
  const [result, setResult] = useState<Result<T>>({ key: null });

  useEffect(() => {
    if (path === null || key === null) return;
    let active = true;
    api<T>(path)
      .then((data) => {
        if (active) setResult({ key, data });
      })
      .catch((error: Error) => {
        if (active) setResult((prev) => ({ key, data: prev.data, error }));
      });
    return () => {
      active = false;
    };
  }, [path, key]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  const settled = result.key === key;
  return {
    data: result.data,
    error: settled ? result.error : undefined,
    loading: key !== null && !settled,
    reload,
  };
}
