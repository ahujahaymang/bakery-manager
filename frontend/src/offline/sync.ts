/**
 * Offline sale sync — drain the {@link cartQueue} to the Backend when
 * connectivity is restored (Req 18.4, 18.5, 18.6).
 *
 * Policy implemented here (design "Offline" bullet, Property 18):
 *
 * 1. **Drain on reconnect within 30s (Req 18.4).** {@link startOfflineSync}
 *    listens for the browser `online` event; on reconnect it kicks off a drain
 *    that is bounded by a 30-second deadline so every offline sale is submitted
 *    within 30 seconds of connectivity returning.
 *
 * 2. **At most once (Req 18.5).** Each sale is replayed with the idempotency
 *    key it was stamped with at enqueue time, sent as the `Idempotency-Key`
 *    header. The Backend returns the already-created order on a duplicate key,
 *    so a restored connection never creates a second sale record. A sale is
 *    only removed from the local queue after the Backend confirms persistence
 *    (fresh create *or* idempotent replay), so a crash mid-drain re-submits the
 *    same key rather than losing or duplicating the sale.
 *
 * 3. **Bounded retries, then report + retain (Req 18.6).** A sale that fails to
 *    submit is retried up to {@link MAX_RETRIES} (3) times. After the third
 *    failure it is **reported** to the user (via the {@link onPermanentFailure}
 *    callback / {@link SYNC_FAILURE_EVENT}) and **retained** in the queue for a
 *    later manual/again-online attempt — it is never silently dropped.
 */

import { sell } from "../api/endpoints";
import { ApiError } from "../api/client";
import {
  listQueuedSales,
  removeQueuedSale,
  updateRetryCount,
  type QueuedSale,
} from "./cartQueue";

/** Req 18.6 — a sale is retried at most 3 times before it is reported. */
export const MAX_RETRIES = 3;

/** Req 18.4 — every offline sale must be submitted within 30s of reconnect. */
export const DRAIN_DEADLINE_MS = 30_000;

/**
 * Custom event dispatched on `window` when a sale is permanently failed so UI
 * layers can surface it (Req 18.6). `detail` carries the offending sale.
 */
export const SYNC_FAILURE_EVENT = "kitchenos:offline-sync-failure";

/** Outcome of draining a single sale. */
export type DrainOutcome = "synced" | "retained" | "failed";

/** Aggregate result of a single drain pass, useful for tests and indicators. */
export interface DrainResult {
  /** Idempotency keys of sales confirmed persisted server-side (removed). */
  synced: string[];
  /** Sales that failed this pass but still have retries left (retained). */
  retained: QueuedSale[];
  /** Sales that exhausted their retries — reported and retained (Req 18.6). */
  failed: QueuedSale[];
}

/** Optional hooks so a UI layer can react to drain progress without polling. */
export interface SyncHooks {
  /**
   * Invoked once per sale that has failed all {@link MAX_RETRIES} attempts.
   * The sale remains in local storage (Req 18.6); this is the "report" half.
   */
  onPermanentFailure?: (sale: QueuedSale) => void;
  /** Invoked after a sale is confirmed persisted and removed from the queue. */
  onSynced?: (idempotencyKey: string) => void;
}

/** True when the runtime believes it currently has network connectivity. */
function isOnline(): boolean {
  // `navigator.onLine === false` is a reliable "definitely offline" signal;
  // treat anything else (true or unknown) as online and let the request decide.
  return typeof navigator === "undefined" || navigator.onLine !== false;
}

/** Report a permanently-failed sale (Req 18.6): callback + DOM event. */
function reportPermanentFailure(sale: QueuedSale, hooks?: SyncHooks): void {
  hooks?.onPermanentFailure?.(sale);
  if (typeof window !== "undefined" && typeof CustomEvent === "function") {
    try {
      window.dispatchEvent(
        new CustomEvent<QueuedSale>(SYNC_FAILURE_EVENT, { detail: sale })
      );
    } catch {
      // Event dispatch is best-effort; the sale is still retained in the queue.
    }
  }
}

/**
 * A permanent (non-retryable) client error means retrying with the same body
 * will keep failing, so we should not waste the retry budget on it. Auth,
 * validation, and not-found are permanent; timeouts/network/5xx are transient.
 */
function isPermanentError(err: unknown): boolean {
  if (err instanceof ApiError) {
    if (err.isTimeout || err.isNetworkError) return false;
    // 5xx are transient server issues worth retrying.
    if (err.status >= 500) return false;
    // 4xx (validation, unauthorized, forbidden, not_found, conflict) won't
    // succeed on replay of the identical body.
    if (err.status >= 400) return true;
  }
  return false;
}

/**
 * Submit a single queued sale exactly once (Req 18.5) and reconcile the queue.
 *
 * - **Success** (fresh create or idempotent replay): the sale is removed so it
 *   is never submitted again.
 * - **Transient failure**: the retry count is bumped; the sale is retained.
 *   When it has now failed {@link MAX_RETRIES} times it is reported (Req 18.6).
 * - **Permanent client error**: the retry budget is exhausted immediately, the
 *   sale is reported, and it is retained for manual inspection (Req 18.6).
 */
async function drainSale(sale: QueuedSale, hooks?: SyncHooks): Promise<DrainOutcome> {
  try {
    // Replay with the sale's idempotency key → at most one order (Req 18.5).
    await sell.checkout(sale.body, sale.idempotencyKey);
    await removeQueuedSale(sale.idempotencyKey);
    hooks?.onSynced?.(sale.idempotencyKey);
    return "synced";
  } catch (err) {
    const message =
      err instanceof Error ? err.message : "Failed to submit offline sale.";

    // A permanent client error can't be fixed by retrying the same payload;
    // jump straight to the reported+retained terminal state (Req 18.6).
    const nextRetryCount = isPermanentError(err)
      ? MAX_RETRIES
      : sale.retryCount + 1;

    await updateRetryCount(sale.idempotencyKey, nextRetryCount, message);

    if (nextRetryCount >= MAX_RETRIES) {
      // Req 18.6 — report the failure and retain the sale in local storage.
      reportPermanentFailure(
        { ...sale, retryCount: nextRetryCount, lastError: message },
        hooks
      );
      return "failed";
    }
    return "retained";
  }
}

/**
 * Make a single pass over the queue, attempting to submit every sale that has
 * not already exhausted its retries. Stops early if the {@link deadline} passes
 * so the caller's 30s reconnect budget (Req 18.4) is respected.
 *
 * @param deadline epoch millis after which no further sales are attempted.
 */
export async function drainQueueOnce(
  hooks?: SyncHooks,
  deadline = Date.now() + DRAIN_DEADLINE_MS
): Promise<DrainResult> {
  const result: DrainResult = { synced: [], retained: [], failed: [] };

  const sales = await listQueuedSales();
  for (const sale of sales) {
    // Respect the reconnect deadline (Req 18.4) and give up if we go offline
    // again mid-drain — remaining sales stay queued for the next reconnect.
    if (Date.now() >= deadline || !isOnline()) break;

    // A sale that already used its full retry budget is left reported+retained.
    if (sale.retryCount >= MAX_RETRIES) {
      result.failed.push(sale);
      continue;
    }

    const outcome = await drainSale(sale, hooks);
    if (outcome === "synced") result.synced.push(sale.idempotencyKey);
    else if (outcome === "retained") result.retained.push(sale);
    else result.failed.push(sale);
  }

  return result;
}

/**
 * Drain the queue on reconnect within the 30-second budget (Req 18.4).
 *
 * Retries within the deadline are handled by re-passing over the queue: sales
 * that were merely retained (transient failure, budget remaining) are retried
 * on the next pass until they succeed, exhaust their retries, or the 30s
 * deadline is reached. Sales that failed permanently are left retained.
 */
export async function drainUntilDeadline(hooks?: SyncHooks): Promise<DrainResult> {
  const deadline = Date.now() + DRAIN_DEADLINE_MS;
  const aggregate: DrainResult = { synced: [], retained: [], failed: [] };

  // Loop while there is time left and progress can still be made. Each pass
  // either syncs a sale, advances a retry, or terminates a sale permanently.
  // We stop when a pass makes no further attempts (nothing left to retry).
  // eslint-disable-next-line no-constant-condition
  while (Date.now() < deadline && isOnline()) {
    const pass = await drainQueueOnce(hooks, deadline);
    aggregate.synced.push(...pass.synced);
    aggregate.failed.push(...pass.failed);

    // Nothing was synced and nothing is retryable-remaining → no point looping.
    const madeProgress = pass.synced.length > 0;
    const hasRetryable = pass.retained.length > 0;
    if (!madeProgress && !hasRetryable) {
      aggregate.retained = pass.retained;
      break;
    }
    // Carry the latest retained set forward for the caller.
    aggregate.retained = pass.retained;
    if (!hasRetryable) break;
  }

  return aggregate;
}

// ── Reconnect wiring ─────────────────────────────────────────────────────────

/** Guards against overlapping drains (e.g. rapid online/offline flapping). */
let draining = false;

/**
 * Kick off a drain now if online, coalescing concurrent invocations so only
 * one drain runs at a time. Safe to call from an `online` handler and on app
 * start-up (in case sales were queued in a previous session).
 */
export async function syncNow(hooks?: SyncHooks): Promise<DrainResult> {
  if (draining || !isOnline()) {
    return { synced: [], retained: [], failed: [] };
  }
  draining = true;
  try {
    return await drainUntilDeadline(hooks);
  } finally {
    draining = false;
  }
}

/**
 * Wire up automatic sync: drain once on start-up (to flush sales queued in a
 * previous session) and again whenever connectivity is restored (Req 18.4).
 *
 * Returns a disposer that removes the listener, for teardown in tests / HMR.
 */
export function startOfflineSync(hooks?: SyncHooks): () => void {
  const handleOnline = () => {
    void syncNow(hooks);
  };

  if (typeof window !== "undefined") {
    window.addEventListener("online", handleOnline);
  }

  // Flush anything left over from a prior session, if we're already online.
  void syncNow(hooks);

  return () => {
    if (typeof window !== "undefined") {
      window.removeEventListener("online", handleOnline);
    }
  };
}
