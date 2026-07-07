/// <reference lib="webworker" />
//
// KitchenOS service worker (Workbox, injectManifest source).
//
// Responsibilities:
//   - Precache the app shell (HTML/CSS/JS/icons) so the shell renders offline
//     (Requirements 1.2, 1.4, 18.1).
//   - Runtime-cache the product catalog so the Sell surface can build a cart
//     from cached product data while offline (Requirements 18.1, 18.2).
//
// This file is the source consumed by vite-plugin-pwa's `injectManifest`
// strategy. Vite/Rollup bundle it and replace `self.__WB_MANIFEST` with the
// precache manifest generated from the built app-shell assets.

import { precacheAndRoute, cleanupOutdatedCaches } from "workbox-precaching";
import { registerRoute } from "workbox-routing";
import { NetworkFirst } from "workbox-strategies";
import { ExpirationPlugin } from "workbox-expiration";
import { CacheableResponsePlugin } from "workbox-cacheable-response";

declare const self: ServiceWorkerGlobalScope & {
  __WB_MANIFEST: Array<{ url: string; revision: string | null }>;
};

// ── App-shell precache (Req 1.2, 1.4, 18.1) ────────────────────────────────
// `self.__WB_MANIFEST` is injected at build time with the hashed app-shell
// assets (HTML, CSS, JS, icons). Precaching them lets the shell render with no
// network request when the device is offline.
cleanupOutdatedCaches();
precacheAndRoute(self.__WB_MANIFEST || []);

// ── API read runtime cache (Req 18.1, 18.2) ────────────────────────────────
// Every tab reads its data from a `GET /api/v1/...` endpoint (sell session,
// orders, inventory, recipes, customers, invoices, expenses, insights). Use
// NetworkFirst across all of them so an online device always sees the *current*
// data immediately after a write (e.g. adding a Sell item, creating an order) —
// StaleWhileRevalidate would serve the pre-write cached copy and only refresh
// in the background, forcing a manual reload to see the change. When the network
// is unavailable it falls back to the last-seen response so the surfaces (in
// particular the Sell cart) still render offline (Req 18.1, 18.2).
const API_CACHE = "kitchenos-api";
registerRoute(
  ({ url, request }) =>
    request.method === "GET" && /^\/api\/v1\//.test(url.pathname),
  new NetworkFirst({
    cacheName: API_CACHE,
    plugins: [
      // Only cache successful, complete responses.
      new CacheableResponsePlugin({ statuses: [0, 200] }),
      // Keep the cache bounded and reasonably fresh (24h).
      new ExpirationPlugin({
        maxEntries: 128,
        maxAgeSeconds: 24 * 60 * 60,
      }),
    ],
  })
);

// ── App-shell navigation fallback (Req 1.4, 18.1) ──────────────────────────
// Serve the precached index shell for navigations so launching the installed
// app offline renders the shell. NetworkFirst prefers a fresh document when
// online but falls back to the cached shell when the network is unavailable.
registerRoute(
  ({ request }) => request.mode === "navigate",
  new NetworkFirst({
    cacheName: "kitchenos-shell",
    plugins: [new CacheableResponsePlugin({ statuses: [0, 200] })],
  })
);

// Activate a freshly installed worker immediately so caching (and therefore
// offline capability) is available as soon as possible after first load.
self.addEventListener("install", () => {
  void self.skipWaiting();
});
self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});
