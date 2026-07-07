/**
 * API client — the single fetch wrapper every domain call goes through.
 *
 * Responsibilities (design §"API client", Req 20.2, 20.3):
 *
 * 1. **Transport (Req 20.2).** Every domain operation talks to the Backend over
 *    HTTP against the versioned `/api/v1` surface. The SPA is served
 *    same-origin (mounted at `/app`, API at `/api/v1`), so requests are made
 *    with a relative base URL and `credentials: "include"` — this sends the
 *    httpOnly `device_session` cookie automatically when present.
 *
 * 2. **Device-token attachment (httpOnly cookie preferred, Bearer fallback).**
 *    The device session token is delivered in an httpOnly, Secure, SameSite
 *    cookie by preference (it cannot be read by JS, which mitigates XSS token
 *    theft) and is sent automatically by `credentials: "include"`. For installed
 *    PWA fetch contexts where the cookie is not available, a raw token may be
 *    registered via {@link setDeviceToken}; when present it is attached as an
 *    `Authorization: Bearer <token>` header as a fallback. Server-side
 *    (`app/api/deps.py`) resolves either source, preferring the Bearer header.
 *
 * 3. **30-second abort timeout (Req 20.3).** Each request is bounded by a 30s
 *    `AbortController` timeout. If no successful response arrives in time the
 *    request is terminated and a typed {@link ApiError} with `isTimeout: true`
 *    is thrown so the UI can display a communication-failure indication.
 *
 * 4. **Error surfacing that retains unsaved input (Req 20.3).** On any failure
 *    (timeout, network error, or non-2xx response) the client **throws** a typed
 *    {@link ApiError}. It never clears, resets, or otherwise mutates caller
 *    state, so a component that awaited a call keeps whatever unsaved input the
 *    user had entered — the caller decides how to surface the error and is free
 *    to leave its form untouched for a retry.
 */

/** Where the versioned REST API is mounted. Relative so same-origin cookies flow. */
export const API_BASE_URL = "/api/v1";

/** Req 20.3 — terminate any request that has not completed within 30 seconds. */
export const REQUEST_TIMEOUT_MS = 30_000;

/**
 * Machine-readable error codes returned by the backend error mapping
 * (`app/api/errors.py`). `timeout` / `network` are client-side additions the
 * server never emits.
 */
export type ApiErrorCode =
  | "unauthorized"
  | "forbidden"
  | "validation_error"
  | "conflict"
  | "not_found"
  | "invalid_transition"
  | "extraction_failed"
  | "otp_delivery_failed"
  | "locked_out"
  | "error"
  | "timeout"
  | "network";

/** Shape of the JSON error bodies produced by `app/api/errors.py`. */
export interface ApiErrorBody {
  error: ApiErrorCode | string;
  detail?: string;
  field?: string;
  retry_after?: number;
}

/**
 * Typed error thrown for every non-successful request.
 *
 * Throwing (rather than returning a sentinel or mutating state) is deliberate:
 * it lets `await`ing callers catch the failure and surface an error indication
 * while leaving their unsaved form input exactly as the user left it (Req 20.3).
 */
export class ApiError extends Error {
  /** HTTP status code, or 0 for client-side failures (timeout / network). */
  readonly status: number;
  /** Machine-readable error code (server `error` field, or `timeout`/`network`). */
  readonly code: ApiErrorCode | string;
  /** The offending field name for validation / conflict errors, when provided. */
  readonly field?: string;
  /** Seconds to wait before retrying, for `locked_out` (429) responses. */
  readonly retryAfter?: number;
  /** True when the request was aborted by the 30s timeout (Req 20.3). */
  readonly isTimeout: boolean;
  /** True when the request failed before any HTTP response (offline, DNS, TLS). */
  readonly isNetworkError: boolean;
  /** The parsed error body, when the response carried one. */
  readonly body?: ApiErrorBody;

  constructor(params: {
    message: string;
    status: number;
    code: ApiErrorCode | string;
    field?: string;
    retryAfter?: number;
    isTimeout?: boolean;
    isNetworkError?: boolean;
    body?: ApiErrorBody;
  }) {
    super(params.message);
    this.name = "ApiError";
    this.status = params.status;
    this.code = params.code;
    this.field = params.field;
    this.retryAfter = params.retryAfter;
    this.isTimeout = params.isTimeout ?? false;
    this.isNetworkError = params.isNetworkError ?? false;
    this.body = params.body;
    // Restore prototype chain for `instanceof` under transpiled targets.
    Object.setPrototypeOf(this, ApiError.prototype);
  }
}

// ── Device-token registry (Bearer fallback) ─────────────────────────────────
//
// The httpOnly cookie is the primary carrier and is invisible to JS. This
// in-memory slot only holds a raw token for the Bearer fallback path used by
// installed-PWA fetch contexts. It is intentionally not persisted here; the
// auth layer decides whether/where to persist and calls setDeviceToken.

let deviceToken: string | null = null;

/** Register a raw device session token to attach as a Bearer fallback header. */
export function setDeviceToken(token: string | null): void {
  deviceToken = token && token.trim() ? token.trim() : null;
}

/** Return the currently registered Bearer-fallback token, if any. */
export function getDeviceToken(): string | null {
  return deviceToken;
}

/** Options accepted by {@link apiFetch}, extending the standard fetch init. */
export interface ApiRequestOptions extends Omit<RequestInit, "body" | "signal"> {
  /**
   * JSON-serializable request body. Mutually exclusive with {@link formData}.
   * When set, the body is JSON-encoded and `Content-Type: application/json` is
   * added automatically.
   */
  json?: unknown;
  /**
   * A `FormData` body for multipart uploads (e.g. ingestion image extract).
   * The browser sets the multipart `Content-Type`/boundary itself, so no
   * `Content-Type` header is added.
   */
  formData?: FormData;
  /** Query parameters appended to the URL (undefined/null values are skipped). */
  query?: Record<string, string | number | boolean | null | undefined>;
  /** Per-request timeout override in ms. Defaults to {@link REQUEST_TIMEOUT_MS}. */
  timeoutMs?: number;
  /**
   * Expected successful response type. `json` (default) parses and returns the
   * body; `blob` returns a `Blob`; `void` ignores the body (e.g. 204 responses).
   */
  responseType?: "json" | "blob" | "void";
}

/** Build a fully-qualified URL from a path + optional query parameters. */
function buildUrl(path: string, query?: ApiRequestOptions["query"]): string {
  const base = path.startsWith("/api/") ? path : `${API_BASE_URL}${path}`;
  if (!query) return base;

  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === null || value === undefined) continue;
    params.append(key, String(value));
  }
  const qs = params.toString();
  return qs ? `${base}?${qs}` : base;
}

/** Best-effort parse of a JSON error body; returns undefined when absent. */
async function parseErrorBody(response: Response): Promise<ApiErrorBody | undefined> {
  const contentType = response.headers.get("Content-Type") || "";
  if (!contentType.includes("application/json")) return undefined;
  try {
    return (await response.json()) as ApiErrorBody;
  } catch {
    return undefined;
  }
}

/** Turn a non-2xx response into a typed {@link ApiError} with a UI-ready message. */
async function errorFromResponse(response: Response): Promise<ApiError> {
  const body = await parseErrorBody(response);
  const code = (body?.error as ApiErrorCode) || "error";

  // Prefer the server-provided detail; otherwise a friendly per-code default.
  const message =
    body?.detail || defaultMessageForCode(code, response.status);

  return new ApiError({
    message,
    status: response.status,
    code,
    field: body?.field,
    retryAfter: body?.retry_after,
    body,
  });
}

/** Human-readable fallback messages when the server sends no `detail`. */
function defaultMessageForCode(code: string, status: number): string {
  switch (code) {
    case "unauthorized":
      return "Your session has expired. Please sign in again.";
    case "forbidden":
      return "You do not have permission to do that.";
    case "not_found":
      return "We couldn't find what you were looking for.";
    case "conflict":
      return "That conflicts with an existing record.";
    case "invalid_transition":
      return "That change isn't allowed from the current state.";
    case "extraction_failed":
      return "We couldn't read that image. Try a clearer photo.";
    case "otp_delivery_failed":
      return "We couldn't send the code. Please try again.";
    case "locked_out":
      return "Too many attempts. Please wait and try again.";
    case "validation_error":
      return "Please check the highlighted fields and try again.";
    default:
      return `Request failed (${status}).`;
  }
}

/**
 * Core fetch wrapper. Attaches the device token, enforces the 30s timeout, and
 * throws a typed {@link ApiError} on any failure — never mutating caller state
 * (Req 20.2, 20.3).
 *
 * @typeParam T - the expected parsed response type.
 */
export async function apiFetch<T>(
  path: string,
  options: ApiRequestOptions = {}
): Promise<T> {
  const {
    json,
    formData,
    query,
    timeoutMs = REQUEST_TIMEOUT_MS,
    responseType = "json",
    headers,
    ...init
  } = options;

  const url = buildUrl(path, query);

  const finalHeaders = new Headers(headers);
  finalHeaders.set("Accept", "application/json");

  // Bearer fallback: the httpOnly cookie (sent via credentials:"include") is
  // preferred; when a raw token is registered we also attach it as a header.
  const token = getDeviceToken();
  if (token && !finalHeaders.has("Authorization")) {
    finalHeaders.set("Authorization", `Bearer ${token}`);
  }

  let body: BodyInit | undefined;
  if (formData !== undefined) {
    body = formData; // Browser sets multipart Content-Type + boundary.
  } else if (json !== undefined) {
    body = JSON.stringify(json);
    if (!finalHeaders.has("Content-Type")) {
      finalHeaders.set("Content-Type", "application/json");
    }
  }

  // Req 20.3 — bound the request with a 30s abort timeout.
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  let response: Response;
  try {
    response = await fetch(url, {
      ...init,
      headers: finalHeaders,
      body,
      credentials: "include", // send the httpOnly device_session cookie
      signal: controller.signal,
    });
  } catch (err) {
    // Distinguish an abort (timeout, Req 20.3) from a genuine network failure.
    // In both cases we throw and touch no caller state, so unsaved input stays.
    if (controller.signal.aborted) {
      throw new ApiError({
        message:
          "The request took too long and was cancelled. Your input has been kept — please try again.",
        status: 0,
        code: "timeout",
        isTimeout: true,
      });
    }
    throw new ApiError({
      message:
        "We couldn't reach the server. Check your connection and try again.",
      status: 0,
      code: "network",
      isNetworkError: true,
    });
  } finally {
    clearTimeout(timer);
  }

  if (!response.ok) {
    throw await errorFromResponse(response);
  }

  if (responseType === "void" || response.status === 204) {
    return undefined as T;
  }
  if (responseType === "blob") {
    return (await response.blob()) as T;
  }

  // Empty body with a JSON responseType → return undefined rather than throw.
  const text = await response.text();
  if (!text) return undefined as T;
  return JSON.parse(text) as T;
}

/** Convenience helpers for the common verbs used by `endpoints.ts`. */
export const http = {
  get: <T>(path: string, options?: ApiRequestOptions) =>
    apiFetch<T>(path, { ...options, method: "GET" }),
  post: <T>(path: string, options?: ApiRequestOptions) =>
    apiFetch<T>(path, { ...options, method: "POST" }),
  patch: <T>(path: string, options?: ApiRequestOptions) =>
    apiFetch<T>(path, { ...options, method: "PATCH" }),
  put: <T>(path: string, options?: ApiRequestOptions) =>
    apiFetch<T>(path, { ...options, method: "PUT" }),
  delete: <T>(path: string, options?: ApiRequestOptions) =>
    apiFetch<T>(path, { ...options, method: "DELETE" }),
};
