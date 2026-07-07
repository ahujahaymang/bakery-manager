// Service-worker registration with graceful degradation.
//
// Registers the Workbox service worker (built from src/sw.ts) on first load
// using workbox-window (Req 1.2). If registration or the initial precache
// fails for any reason, the app keeps working over the network and an
// "offline capability unavailable" indication is surfaced without blocking
// access to app functionality (Req 1.3).

import { Workbox } from "workbox-window";

/**
 * Name of the custom event dispatched on `window` describing the offline
 * capability status. UI layers (added in later tasks) can listen for this to
 * render a persistent indicator; a minimal DOM banner fallback is also shown.
 */
export const OFFLINE_STATUS_EVENT = "kitchenos:offline-status";

export interface OfflineStatusDetail {
  /** True when the service worker is active and offline capability is ready. */
  available: boolean;
  /** Human-readable reason when `available` is false. */
  reason?: string;
}

function broadcastStatus(detail: OfflineStatusDetail): void {
  try {
    window.dispatchEvent(
      new CustomEvent<OfflineStatusDetail>(OFFLINE_STATUS_EVENT, { detail })
    );
  } catch {
    // CustomEvent is universally supported in target browsers; ignore if not.
  }
}

/**
 * Show a lightweight, non-blocking banner indicating offline capability is
 * unavailable (Req 1.3). This is a fallback for before the full UI exists; it
 * never blocks app functionality and can be dismissed.
 */
function showOfflineUnavailableIndication(reason: string): void {
  broadcastStatus({ available: false, reason });

  const existing = document.getElementById("offline-capability-indicator");
  if (existing) {
    return;
  }
  const banner = document.createElement("div");
  banner.id = "offline-capability-indicator";
  banner.setAttribute("role", "status");
  banner.setAttribute("aria-live", "polite");
  banner.textContent =
    "Offline capability is unavailable. The app will keep working while you're online.";
  banner.style.cssText = [
    "position:fixed",
    "left:0",
    "right:0",
    "bottom:0",
    "z-index:2147483647",
    "padding:8px 12px",
    "font:14px/1.4 system-ui,sans-serif",
    "text-align:center",
    "color:#fff",
    "background:#b45309",
  ].join(";");
  // Append when the body is ready; the app remains fully usable regardless.
  const attach = () => document.body?.appendChild(banner);
  if (document.body) {
    attach();
  } else {
    window.addEventListener("DOMContentLoaded", attach, { once: true });
  }
}

/**
 * Register the service worker. Resolves regardless of outcome; failures are
 * reported via the offline-unavailable indication and never throw to callers.
 */
export async function registerServiceWorker(): Promise<void> {
  if (!("serviceWorker" in navigator)) {
    showOfflineUnavailableIndication("Service workers are not supported.");
    return;
  }

  // The plugin emits the compiled worker at <base>sw.js with the app scope.
  const swUrl = `${import.meta.env.BASE_URL}sw.js`;
  const wb = new Workbox(swUrl, { scope: import.meta.env.BASE_URL });

  // A worker that becomes redundant before activating indicates the install
  // step (which performs precaching) failed — treat as caching failure.
  wb.addEventListener("redundant", () => {
    showOfflineUnavailableIndication(
      "The offline cache could not be prepared."
    );
  });

  try {
    const registration = await wb.register();
    if (!registration) {
      showOfflineUnavailableIndication(
        "The service worker could not be registered."
      );
      return;
    }
    // Successful registration; offline capability is available once active.
    broadcastStatus({ available: true });
  } catch (error) {
    // Registration or the initial precache failed (e.g. blocked, HTTP context,
    // or a caching error). Degrade gracefully (Req 1.3).
    const reason =
      error instanceof Error ? error.message : "Service worker registration failed.";
    showOfflineUnavailableIndication(reason);
  }
}
