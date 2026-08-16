/**
 * Load something, remember why it failed, and be able to try again.
 *
 * Every view had grown its own copy of this: a `data` state, an `error` state,
 * an effect that fills one or the other. The copies had drifted -- some kept
 * the HTTP status and some flattened it to a string, which is why a 404 with
 * close-name suggestions and a dead server rendered identically. Presenting a
 * failure properly needs the status, so it is kept here rather than discarded
 * at each call site.
 *
 * `reload` re-runs the exact request that failed, which is what makes a Retry
 * button honest: it retries the thing, rather than reloading the page and
 * hoping.
 */
import { useCallback, useEffect, useState } from "react";
import type { Result } from "@/lib/api";

export interface Failed {
  status: number;
  message: string;
  didYouMean?: string[];
}

export interface Loaded<T> {
  data: T | null;
  error: Failed | null;
  /** True only for the first load, so a retry does not blank the screen. */
  loading: boolean;
  reload: () => void;
}

export function useLoad<T>(
  request: () => Promise<Result<T>>,
  deps: unknown[] = [],
): Loaded<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<Failed | null>(null);
  const [loading, setLoading] = useState(true);
  const [attempt, setAttempt] = useState(0);

  // The request closure is rebuilt on every render, so it cannot be a
  // dependency without looping. The caller's `deps` are the real signal for
  // when the request has changed, exactly as with `useEffect`.
  const run = useCallback(request, deps);

  useEffect(() => {
    // Guards against a slow first response overwriting a fast second one after
    // the user has already switched model or tab -- the classic race that
    // shows one model's figures under another's name.
    let current = true;
    setLoading(true);
    void (async () => {
      const result = await run();
      if (!current) return;
      if (result.ok) {
        setData(result.data);
        setError(null);
      } else {
        setError({
          status: result.status,
          message: result.message,
          didYouMean: result.didYouMean,
        });
        setData(null);
      }
      setLoading(false);
    })();
    return () => {
      current = false;
    };
  }, [run, attempt]);

  const reload = useCallback(() => setAttempt((n) => n + 1), []);

  return { data, error, loading, reload };
}
