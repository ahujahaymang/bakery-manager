/**
 * Offline cart queue — durable IndexedDB store of sales completed while the
 * device had no connectivity (Req 18.3).
 *
 * When a checkout is completed offline the Sell surface records the completed
 * sale in local device storage (Req 18.3). Each queued sale is stamped, at
 * enqueue time, with a **client-generated idempotency key** (Req 18.5): this
 * key is later replayed to `POST /sell/checkout` as the `Idempotency-Key`
 * header so a restored connection can never create a duplicate order — the
 * Backend maps `(tenant_id, idempotency_key) → order_id` and returns the
 * already-created order on a replay (see `app/api/sell_router.py`).
 *
 * This module owns **only** the durable queue and its CRUD surface:
 *   - {@link enqueueSale}         persist a completed offline sale + fresh key
 *   - {@link listQueuedSales}     read every queued sale (FIFO by enqueue time)
 *   - {@link removeQueuedSale}    drop a sale once it has been persisted server-side
 *   - {@link updateRetryCount}    record a failed attempt's retry count + last error
 *
 * Draining the queue on reconnect (at-most-once, ≤3 retries, report+retain on
 * permanent failure) lives in `sync.ts` (Req 18.4, 18.6). Keeping persistence
 * separate from the drain policy keeps each surface small and testable.
 */

import type { SellCheckoutBody } from "../api/endpoints";

/** IndexedDB database + object store names for the offline sale queue. */
const DB_NAME = "kitchenos-offline";
const DB_VERSION = 1;
const STORE_NAME = "cart-queue";

/**
 * A single completed sale awaiting submission to the Backend.
 *
 * The record is keyed by {@link idempotencyKey} — the same value replayed as
 * the `Idempotency-Key` header — so a sale is stored exactly once locally and
 * can be submitted at most once server-side (Req 18.5).
 */
export interface QueuedSale {
  /**
   * Client-generated idempotency key, created at enqueue time (Req 18.5).
   * Doubles as the IndexedDB primary key so re-enqueueing the same sale is a
   * no-op overwrite rather than a duplicate.
   */
  idempotencyKey: string;
  /** The checkout payload to replay verbatim to `POST /sell/checkout`. */
  body: SellCheckoutBody;
  /** Epoch millis when the sale was recorded offline; used for FIFO ordering. */
  enqueuedAt: number;
  /** Number of submission attempts that have failed so far (Req 18.6). */
  retryCount: number;
  /** Message from the most recent failed attempt, for reporting (Req 18.6). */
  lastError?: string;
}

/**
 * Generate a collision-resistant idempotency key. Prefers the platform
 * `crypto.randomUUID`; falls back to a random+time composite for older
 * runtimes that lack it (still unique enough for per-device sale keys).
 */
export function generateIdempotencyKey(): string {
  const c = (globalThis as { crypto?: Crypto }).crypto;
  if (c && typeof c.randomUUID === "function") {
    return c.randomUUID();
  }
  // Fallback: timestamp + two random segments.
  const rand = () => Math.random().toString(36).slice(2, 10);
  return `sale-${Date.now().toString(36)}-${rand()}${rand()}`;
}

/** Open (creating/upgrading on first use) the offline queue database. */
function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);

    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        // Keyed by the idempotency key; an index on enqueuedAt lets us read
        // the queue back in the order sales were completed (FIFO drain).
        const store = db.createObjectStore(STORE_NAME, {
          keyPath: "idempotencyKey",
        });
        store.createIndex("enqueuedAt", "enqueuedAt", { unique: false });
      }
    };

    request.onsuccess = () => resolve(request.result);
    request.onerror = () =>
      reject(request.error ?? new Error("Failed to open offline queue database"));
  });
}

/** Run `work` against the store within a transaction and resolve on commit. */
function withStore<T>(
  mode: IDBTransactionMode,
  work: (store: IDBObjectStore) => IDBRequest<T> | void
): Promise<T | undefined> {
  return openDb().then(
    (db) =>
      new Promise<T | undefined>((resolve, reject) => {
        const tx = db.transaction(STORE_NAME, mode);
        const store = tx.objectStore(STORE_NAME);
        let result: T | undefined;

        const request = work(store);
        if (request) {
          request.onsuccess = () => {
            result = request.result;
          };
          request.onerror = () =>
            reject(request.error ?? new Error("Offline queue request failed"));
        }

        tx.oncomplete = () => {
          db.close();
          resolve(result);
        };
        tx.onabort = tx.onerror = () => {
          db.close();
          reject(tx.error ?? new Error("Offline queue transaction failed"));
        };
      })
  );
}

/**
 * Persist a completed offline sale, stamping it with a fresh idempotency key
 * (Req 18.3, 18.5). Returns the created {@link QueuedSale} so the caller can
 * surface the key (e.g. on a provisional receipt).
 */
export async function enqueueSale(body: SellCheckoutBody): Promise<QueuedSale> {
  const sale: QueuedSale = {
    idempotencyKey: generateIdempotencyKey(),
    body,
    enqueuedAt: Date.now(),
    retryCount: 0,
  };
  await withStore("readwrite", (store) => store.add(sale));
  return sale;
}

/**
 * Read every queued sale in FIFO order (oldest completed sale first) so the
 * drain in `sync.ts` submits sales in the order they were made.
 */
export async function listQueuedSales(): Promise<QueuedSale[]> {
  const sales =
    (await withStore<QueuedSale[]>("readonly", (store) =>
      store.getAll() as IDBRequest<QueuedSale[]>
    )) ?? [];
  return sales.sort((a, b) => a.enqueuedAt - b.enqueuedAt);
}

/** Fetch a single queued sale by its idempotency key, if present. */
export async function getQueuedSale(
  idempotencyKey: string
): Promise<QueuedSale | undefined> {
  return withStore<QueuedSale | undefined>("readonly", (store) =>
    store.get(idempotencyKey) as IDBRequest<QueuedSale | undefined>
  ).then((sale) => sale ?? undefined);
}

/**
 * Remove a sale from the queue. Called once the Backend has confirmed the sale
 * is persisted (either freshly created or returned as an idempotent replay),
 * so it is never submitted again (Req 18.5).
 */
export async function removeQueuedSale(idempotencyKey: string): Promise<void> {
  await withStore("readwrite", (store) => store.delete(idempotencyKey));
}

/**
 * Record that an attempt to submit a queued sale failed: bump its retry count
 * and store the error message for reporting (Req 18.6). The sale is retained
 * in the queue — this only updates its bookkeeping.
 */
export async function updateRetryCount(
  idempotencyKey: string,
  retryCount: number,
  lastError?: string
): Promise<void> {
  await withStore("readwrite", (store) => {
    const getReq = store.get(idempotencyKey) as IDBRequest<QueuedSale | undefined>;
    getReq.onsuccess = () => {
      const existing = getReq.result;
      if (!existing) return; // Sale already drained/removed; nothing to update.
      existing.retryCount = retryCount;
      existing.lastError = lastError;
      store.put(existing);
    };
  });
}

/** Count of sales currently awaiting submission (for badges/indicators). */
export async function queuedSaleCount(): Promise<number> {
  const count = await withStore<number>("readonly", (store) =>
    store.count() as IDBRequest<number>
  );
  return count ?? 0;
}
