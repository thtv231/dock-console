"use client";

import { useCallback } from "react";
import { useKey } from "@/lib/key";

export type Mode = "replay" | "live";

export type MeterCall = {
  op: string;
  model: string | null;
  ms: number;
  tokens?: number | null;
  tokens_in?: number | null;
  tokens_out?: number | null;
  error?: string;
};

export type ApiError = { error: string; status: number; message?: string; mode?: Mode };

export type Result<T> = { ok: true; data: T } | { ok: false; error: ApiError };

// What a failed request means for the person looking at the screen.
export function describeError(e: ApiError): string {
  switch (e.error) {
    case "not_recorded":
      return "No recording for this one. Add your Mistral key to run it live.";
    case "no_key":
      return "This needs a Mistral key. Add one to run it live.";
    case "rate_limited":
      return "Live rate limit reached. Wait a minute and run it again.";
    case "mistral_error":
      return e.status === 401 || e.status === 403
        ? "Mistral rejected the key. Check it and set it again."
        : `Mistral returned an error${e.message ? `: ${e.message}` : "."}`;
    case "mistral_unreachable":
      return "Mistral did not respond. Run it again in a moment.";
    case "api_unreachable":
      return "The API is not reachable. Start it with: uvicorn api.index:app --port 8000";
    default:
      return e.message ?? `Request failed (${e.error}, HTTP ${e.status}).`;
  }
}

async function call<T>(path: string, key: string | null, init?: RequestInit): Promise<Result<T>> {
  const headers = new Headers(init?.headers);
  if (key) headers.set("X-Mistral-Key", key);
  let response: Response;
  try {
    response = await fetch(path, { ...init, headers, cache: "no-store" });
  } catch {
    return { ok: false, error: { error: "api_unreachable", status: 0 } };
  }
  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    // The Next dev proxy answers with plain text when uvicorn is down.
    return { ok: false, error: { error: response.status >= 500 ? "api_unreachable" : "bad_response", status: response.status } };
  }
  if (!response.ok) {
    const b = (body ?? {}) as Partial<ApiError> & { mistral_status?: number };
    return {
      ok: false,
      error: { error: b.error ?? "http_error", status: b.mistral_status ?? response.status, message: b.message, mode: b.mode },
    };
  }
  return { ok: true, data: body as T };
}

/** fetch for /api/*, adding X-Mistral-Key from the in-memory key when one is set. */
export function useApi() {
  const { key } = useKey();
  const get = useCallback(<T,>(path: string) => call<T>(path, key), [key]);
  const post = useCallback(
    <T,>(path: string, body: unknown) =>
      call<T>(path, key, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
    [key],
  );
  return { get, post, live: key !== null };
}
