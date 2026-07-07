/**
 * Sell — the point-of-sale surface (Requirements 8.1, 8.2, 8.4, 8.5).
 *
 * A fast, tap-to-sell screen backed by the existing Register/Booth service
 * layer through `GET /api/v1/sell/session` and `POST /api/v1/sell/checkout`
 * (see `src/api/endpoints.ts`, `app/api/sell_router.py`). It provides:
 *
 *  - **A product grid of everything available for sale** (Req 8.1). The active
 *    session's items are fetched on load, and the last successful catalog is
 *    cached in `localStorage` so the grid still renders when the device is
 *    offline (the design's "runtime cache of the product catalog" / offline
 *    reads, backing the grid without a live request).
 *  - **A cart whose total updates within 1 second of adding a product**
 *    (Req 8.2). Cart state and its derived total are computed synchronously in
 *    React state, so a tap re-renders the total immediately — no awaited call
 *    sits between the tap and the updated total.
 *  - **Cash / UPI payment-method selection** (Req 8.4). The selected method is
 *    sent on the checkout body and recorded on the payment record server-side.
 *  - **Checkout** (Req 8.5, 8.6): online via `sell.checkout` (which sets sales
 *    attribution and enforces one-order/one-payment server-side). When the
 *    device is offline the sale is enqueued to the durable offline cart queue
 *    with a client-generated idempotency key (`src/offline/cartQueue.ts`), so
 *    `src/offline/sync.ts` can replay it at-most-once on reconnect.
 *  - **Receipt + invoice access for a completed (online) sale** (Req 8.5): a
 *    printable receipt (rendered from `sell.getReceipt` and sent to the print
 *    dialog) and a downloadable PDF invoice (base64 from `sell.getInvoice`,
 *    saved via an object URL).
 *
 * Cart totals shown here are a fast client-side preview for the cashier; the
 * payable amount is authoritatively computed by the service layer at checkout
 * (Req 8.6), so the surface never alters the booth's calculated results.
 */

import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  IonBadge,
  IonButton,
  IonButtons,
  IonCard,
  IonCardContent,
  IonCol,
  IonContent,
  IonFooter,
  IonGrid,
  IonHeader,
  IonIcon,
  IonInput,
  IonItem,
  IonItemDivider,
  IonItemGroup,
  IonLabel,
  IonList,
  IonListHeader,
  IonModal,
  IonNote,
  IonPage,
  IonRow,
  IonSegment,
  IonSegmentButton,
  IonSpinner,
  IonText,
  IonTitle,
  IonToolbar,
} from "@ionic/react";
import { addOutline, removeOutline, cloudOfflineOutline } from "ionicons/icons";
import {
  sell,
  ingestion,
  ApiError,
  type SellSession,
  type SellSessionItem,
  type SellCheckoutBody,
  type SaleResponse,
  type ReceiptResponse,
} from "../api/endpoints";
import { useAuth } from "../auth/AuthContext";
import { TabTour } from "../components/TabTour";
import { enqueueSale, generateIdempotencyKey } from "../offline/cartQueue";

/** Payment methods offered at checkout (Req 8.4). */
type PaymentMethod = "cash" | "upi";

/** A cart entry: the catalog item plus the quantity the cashier has tapped. */
interface CartLine {
  item: SellSessionItem;
  quantity: number;
}

/** localStorage key holding the last successful catalog for offline rendering. */
const CATALOG_CACHE_KEY = "kitchenos.sell.session";

/** True when the runtime believes it currently has no connectivity. */
function isOffline(): boolean {
  return typeof navigator !== "undefined" && navigator.onLine === false;
}

/** Read the cached catalog session, tolerating parse/storage errors. */
function readCachedSession(): SellSession | null {
  try {
    const raw = window.localStorage.getItem(CATALOG_CACHE_KEY);
    return raw ? (JSON.parse(raw) as SellSession) : null;
  } catch {
    return null;
  }
}

/** Persist the latest catalog session for offline reads, ignoring storage errors. */
function cacheSession(session: SellSession | null): void {
  try {
    if (session) {
      window.localStorage.setItem(CATALOG_CACHE_KEY, JSON.stringify(session));
    }
  } catch {
    /* storage unavailable (private mode) — live fetch still works */
  }
}

/** Format a monetary amount for display. Values arrive as plain numbers here. */
function formatMoney(value: number, currency = "₹"): string {
  const n = Number.isFinite(value) ? value : 0;
  return `${currency}${n.toFixed(2)}`;
}

/** Extract a user-facing message from an unknown thrown value. */
function messageFor(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error && err.message) return err.message;
  return fallback;
}

export function Sell(): JSX.Element {
  // Product setup (Add item / Upload catalog) is Owner-only; Staff sell only
  // what already exists. The backend also enforces this (require_owner).
  const { role } = useAuth();
  const isOwner = role === "owner";

  const [session, setSession] = useState<SellSession | null>(() => readCachedSession());
  const [loading, setLoading] = useState<boolean>(true);
  const [addItemOpen, setAddItemOpen] = useState<boolean>(false);
  const [uploadOpen, setUploadOpen] = useState<boolean>(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [servingFromCache, setServingFromCache] = useState<boolean>(false);

  // Cart keyed by variant id → quantity. Kept in state so the derived total
  // recomputes synchronously on every tap (Req 8.2).
  const [cart, setCart] = useState<Record<string, number>>({});
  const [paymentMethod, setPaymentMethod] = useState<PaymentMethod>("cash");
  // Optional GST/tax rate (percent) applied at checkout, mirroring order/invoice
  // generation. Blank means no tax. The service layer computes the taxed total.
  const [gstRate, setGstRate] = useState<string>("");

  const [checkingOut, setCheckingOut] = useState<boolean>(false);
  const [checkoutError, setCheckoutError] = useState<string | null>(null);
  const [lastSale, setLastSale] = useState<SaleResponse | null>(null);
  const [queuedOffline, setQueuedOffline] = useState<boolean>(false);

  const [receiptBusy, setReceiptBusy] = useState<boolean>(false);
  const [invoiceBusy, setInvoiceBusy] = useState<boolean>(false);
  const [saleActionError, setSaleActionError] = useState<string | null>(null);

  // ── Load the active session's catalog (Req 8.1) ───────────────────────────
  const loadSession = useCallback(async (): Promise<void> => {
    setLoading(true);
    setLoadError(null);
    try {
      const res = await sell.getSession();
      setSession(res.session);
      setServingFromCache(false);
      cacheSession(res.session);
    } catch (err) {
      // Offline / transient failure: fall back to the cached catalog so the
      // cashier can keep selling and queue the sale (design "offline reads").
      const cached = readCachedSession();
      if (cached) {
        setSession(cached);
        setServingFromCache(true);
        setLoadError(null);
      } else {
        setLoadError(messageFor(err, "Couldn't load products. Pull to retry."));
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadSession();
  }, [loadSession]);

  const items = session?.items ?? [];
  const itemsById = useMemo(() => {
    const map = new Map<string, SellSessionItem>();
    for (const it of items) map.set(it.variant_id, it);
    return map;
  }, [items]);

  // ── Cart derivation — synchronous, so the total updates instantly (Req 8.2) ─
  const cartLines = useMemo<CartLine[]>(() => {
    const lines: CartLine[] = [];
    for (const [variantId, quantity] of Object.entries(cart)) {
      const item = itemsById.get(variantId);
      if (item && quantity > 0) lines.push({ item, quantity });
    }
    return lines;
  }, [cart, itemsById]);

  const cartTotal = useMemo<number>(
    () => cartLines.reduce((sum, { item, quantity }) => sum + item.unit_price * quantity, 0),
    [cartLines]
  );

  const cartCount = useMemo<number>(
    () => cartLines.reduce((sum, { quantity }) => sum + quantity, 0),
    [cartLines]
  );

  // Tax-aware preview of the payable total. This is a client-side preview only;
  // the service layer computes the authoritative taxed total at checkout.
  const gstRateNum = numOrUndefined(gstRate) ?? 0;
  const gstAmount = useMemo<number>(
    () => (gstRateNum > 0 ? cartTotal * (gstRateNum / 100) : 0),
    [cartTotal, gstRateNum]
  );
  const payableTotal = cartTotal + gstAmount;

  const addToCart = useCallback((variantId: string): void => {
    // A fresh tap starts a new sale, so clear any prior completed-sale actions.
    setLastSale(null);
    setQueuedOffline(false);
    setCheckoutError(null);
    setSaleActionError(null);
    setCart((prev) => ({ ...prev, [variantId]: (prev[variantId] ?? 0) + 1 }));
  }, []);

  const decrementCart = useCallback((variantId: string): void => {
    setCart((prev) => {
      const next = { ...prev };
      const q = (next[variantId] ?? 0) - 1;
      if (q <= 0) delete next[variantId];
      else next[variantId] = q;
      return next;
    });
  }, []);

  const clearCart = useCallback((): void => setCart({}), []);

  // ── Checkout (Req 8.4, 8.5, 8.6) ──────────────────────────────────────────
  const buildCheckoutBody = useCallback((): SellCheckoutBody => {
    const gst = numOrUndefined(gstRate);
    return {
      items: cartLines.map(({ item, quantity }) => ({
        variant_id: item.variant_id,
        quantity,
      })),
      payment_method: paymentMethod,
      session_id: session?.session_id ?? null,
      // Only send a tax rate when the cashier entered a positive value; the
      // service layer applies it to the payable total (Req 8.6).
      ...(gst !== undefined && gst > 0 ? { gst_rate: gst } : {}),
    };
  }, [cartLines, paymentMethod, session, gstRate]);

  const handleCheckout = useCallback(async (): Promise<void> => {
    setCheckoutError(null);
    setSaleActionError(null);
    // Guard the empty cart (Req 8.8) — never submit an empty sale.
    if (cartLines.length === 0) {
      setCheckoutError("Add at least one product before checking out.");
      return;
    }

    const body = buildCheckoutBody();
    setCheckingOut(true);
    try {
      if (isOffline()) {
        // Offline: persist the completed sale to the durable queue with a fresh
        // idempotency key so sync.ts can replay it at-most-once (Req 18.3–18.5).
        await enqueueSale(body);
        setQueuedOffline(true);
        setLastSale(null);
        clearCart();
        return;
      }
      // Online: one order + one payment, attributed server-side. The
      // idempotency key makes an accidental resubmit safe (Req 8.3, 18.5).
      const sale = await sell.checkout(body, generateIdempotencyKey());
      setLastSale(sale);
      setQueuedOffline(false);
      clearCart();
    } catch (err) {
      // A network failure mid-request → fall back to the offline queue so the
      // sale is never lost; other failures surface for the cashier (Req 8.7).
      if (err instanceof ApiError && (err.isNetworkError || err.isTimeout)) {
        try {
          await enqueueSale(body);
          setQueuedOffline(true);
          clearCart();
          return;
        } catch {
          /* fall through to reporting the original failure */
        }
      }
      setCheckoutError(messageFor(err, "Checkout failed. Your cart has been kept."));
    } finally {
      setCheckingOut(false);
    }
  }, [cartLines, buildCheckoutBody, clearCart]);

  // ── Receipt + invoice for the completed sale (Req 8.5) ─────────────────────
  const handlePrintReceipt = useCallback(async (): Promise<void> => {
    if (!lastSale) return;
    setSaleActionError(null);
    setReceiptBusy(true);
    try {
      const receipt = await sell.getReceipt(lastSale.order_id);
      openPrintableReceipt(receipt);
    } catch (err) {
      setSaleActionError(messageFor(err, "Couldn't open the receipt. Please try again."));
    } finally {
      setReceiptBusy(false);
    }
  }, [lastSale]);

  const handleDownloadInvoice = useCallback(async (): Promise<void> => {
    if (!lastSale) return;
    setSaleActionError(null);
    setInvoiceBusy(true);
    try {
      const invoice = await sell.getInvoice(lastSale.order_id);
      downloadBase64Pdf(invoice.data, invoice.filename || `invoice-${lastSale.order_id}.pdf`);
    } catch (err) {
      setSaleActionError(messageFor(err, "Couldn't download the invoice. Please try again."));
    } finally {
      setInvoiceBusy(false);
    }
  }, [lastSale]);

  return (
    <IonPage>
      <TabTour
        tabKey="sell"
        title="Sell"
        intro="Your fast tap-to-sell counter."
        points={[
          "Tap products to add them to the cart — the total updates instantly.",
          "Choose Cash or UPI, add GST if you charge it, then checkout.",
          "Owners can add a new item or upload a full catalog to sell.",
          "Print a receipt or download an invoice after each sale.",
        ]}
      />
      <IonHeader>
        <IonToolbar>
          <IonTitle>Sell</IonTitle>
          <IonButtons slot="end">
            {(servingFromCache || isOffline()) && (
              <IonNote color="warning" className="ion-padding-end">
                <IonIcon icon={cloudOfflineOutline} /> Offline
              </IonNote>
            )}
            {/* Product setup — Owner only (Req 8.1). */}
            {isOwner && (
              <>
                <IonButton onClick={() => setAddItemOpen(true)}>Add item</IonButton>
                <IonButton onClick={() => setUploadOpen(true)}>Upload catalog</IonButton>
              </>
            )}
          </IonButtons>
        </IonToolbar>
      </IonHeader>

      <IonContent className="ion-padding">
        {loading && !session ? (
          <div className="ion-text-center ion-padding">
            <IonSpinner name="crescent" />
          </div>
        ) : loadError ? (
          <IonText color="danger">
            <p>{loadError}</p>
            <IonButton fill="outline" onClick={() => void loadSession()}>
              Retry
            </IonButton>
          </IonText>
        ) : items.length === 0 ? (
          isOwner ? (
            <IonText color="medium">
              <p>No products yet. Add your first item or upload your catalog to start selling.</p>
              <div style={{ display: "flex", gap: 8 }}>
                <IonButton onClick={() => setAddItemOpen(true)}>Add item</IonButton>
                <IonButton fill="outline" onClick={() => setUploadOpen(true)}>
                  Upload catalog
                </IonButton>
              </div>
            </IonText>
          ) : (
            <IonText color="medium">
              <p>No products available.</p>
            </IonText>
          )
        ) : (
          <IonGrid>
            <IonRow>
              {items.map((item) => {
                const qty = cart[item.variant_id] ?? 0;
                return (
                  <IonCol size="6" sizeMd="4" sizeLg="3" key={item.variant_id}>
                    <IonCard
                      button
                      onClick={() => addToCart(item.variant_id)}
                      data-testid={`product-${item.variant_id}`}
                    >
                      <IonCardContent>
                        <div>
                          <strong>{item.product_name}</strong>
                        </div>
                        {item.variant_label && (
                          <IonNote color="medium">{item.variant_label}</IonNote>
                        )}
                        <div>{formatMoney(item.unit_price)}</div>
                        {qty > 0 && (
                          <IonBadge color="primary" aria-label={`${qty} in cart`}>
                            {qty}
                          </IonBadge>
                        )}
                      </IonCardContent>
                    </IonCard>
                  </IonCol>
                );
              })}
            </IonRow>
          </IonGrid>
        )}

        {/* Cart */}
        {cartLines.length > 0 && (
          <IonList>
            <IonItem lines="full">
              <IonLabel>
                <h2>Cart</h2>
              </IonLabel>
              <IonButton fill="clear" color="medium" slot="end" onClick={clearCart}>
                Clear
              </IonButton>
            </IonItem>
            {cartLines.map(({ item, quantity }) => (
              <IonItem key={item.variant_id}>
                <IonLabel>
                  <h3>{item.product_name}</h3>
                  <p>
                    {formatMoney(item.unit_price)} × {quantity} ={" "}
                    {formatMoney(item.unit_price * quantity)}
                  </p>
                </IonLabel>
                <IonButtons slot="end">
                  <IonButton
                    fill="outline"
                    onClick={() => decrementCart(item.variant_id)}
                    aria-label={`Remove one ${item.product_name}`}
                  >
                    <IonIcon slot="icon-only" icon={removeOutline} />
                  </IonButton>
                  <IonButton
                    fill="outline"
                    onClick={() => addToCart(item.variant_id)}
                    aria-label={`Add one ${item.product_name}`}
                  >
                    <IonIcon slot="icon-only" icon={addOutline} />
                  </IonButton>
                </IonButtons>
              </IonItem>
            ))}

            {/* GST/tax + totals summary — entered before checkout so the tax is
                clearly part of the payable total (Req 8.6). */}
            <IonItem>
              <IonInput
                label="GST %"
                labelPlacement="stacked"
                type="number"
                inputmode="decimal"
                placeholder="0"
                value={gstRate}
                onIonInput={(e) => setGstRate(e.detail.value ?? "")}
              />
            </IonItem>
            <IonItem lines="none">
              <IonLabel>Subtotal</IonLabel>
              <IonNote slot="end">{formatMoney(cartTotal)}</IonNote>
            </IonItem>
            {gstRateNum > 0 && (
              <IonItem lines="none">
                <IonLabel>GST ({gstRateNum}%)</IonLabel>
                <IonNote slot="end">{formatMoney(gstAmount)}</IonNote>
              </IonItem>
            )}
            <IonItem lines="none">
              <IonLabel>
                <strong>Total</strong>
              </IonLabel>
              <IonNote slot="end" color="dark">
                <strong>{formatMoney(payableTotal)}</strong>
              </IonNote>
            </IonItem>
          </IonList>
        )}

        {/* Completed-sale confirmation + receipt/invoice access (Req 8.5) */}
        {lastSale && (
          <IonCard color="light">
            <IonCardContent>
              <p>
                <strong>Sale complete.</strong> Total{" "}
                {formatMoney(Number(lastSale.total_amount))} ·{" "}
                {lastSale.payment_method ?? paymentMethod}
              </p>
              <IonButton
                expand="block"
                disabled={receiptBusy}
                onClick={() => void handlePrintReceipt()}
              >
                {receiptBusy ? <IonSpinner name="dots" /> : "Print receipt"}
              </IonButton>
              <IonButton
                expand="block"
                fill="outline"
                disabled={invoiceBusy}
                onClick={() => void handleDownloadInvoice()}
              >
                {invoiceBusy ? <IonSpinner name="dots" /> : "Download invoice"}
              </IonButton>
              {saleActionError && (
                <IonText color="danger">
                  <p>{saleActionError}</p>
                </IonText>
              )}
            </IonCardContent>
          </IonCard>
        )}

        {queuedOffline && (
          <IonText color="warning">
            <p>
              You're offline — this sale was saved and will be submitted
              automatically when you're back online.
            </p>
          </IonText>
        )}

        {/* Owner "Add item" — create a product, then reload the grid (Req 8.1). */}
        {isOwner && (
          <IonModal isOpen={addItemOpen} onDidDismiss={() => setAddItemOpen(false)}>
            <AddItemForm
              onClose={() => setAddItemOpen(false)}
              onCreated={() => {
                setAddItemOpen(false);
                void loadSession();
              }}
            />
          </IonModal>
        )}

        {/* Owner "Upload catalog" — scan → edit → confirm, then reload (Req 8.1, 15.6). */}
        {isOwner && (
          <IonModal isOpen={uploadOpen} onDidDismiss={() => setUploadOpen(false)}>
            <UploadCatalogModal
              onClose={() => setUploadOpen(false)}
              onImported={() => {
                setUploadOpen(false);
                void loadSession();
              }}
            />
          </IonModal>
        )}
      </IonContent>

      {/* Sticky checkout bar: payment method + total + action */}
      <IonFooter>
        <IonToolbar>
          <IonSegment
            value={paymentMethod}
            onIonChange={(e) => setPaymentMethod((e.detail.value as PaymentMethod) ?? "cash")}
          >
            <IonSegmentButton value="cash">
              <IonLabel>Cash</IonLabel>
            </IonSegmentButton>
            <IonSegmentButton value="upi">
              <IonLabel>UPI</IonLabel>
            </IonSegmentButton>
          </IonSegment>

          {checkoutError && (
            <IonText color="danger">
              <p className="ion-padding-start">{checkoutError}</p>
            </IonText>
          )}

          <IonButton
            expand="block"
            disabled={checkingOut || cartLines.length === 0}
            onClick={() => void handleCheckout()}
          >
            {checkingOut ? (
              <IonSpinner name="dots" />
            ) : (
              <>
                Checkout {formatMoney(payableTotal)}
                {cartCount > 0 ? ` · ${cartCount} item${cartCount === 1 ? "" : "s"}` : ""}
              </>
            )}
          </IonButton>
        </IonToolbar>
      </IonFooter>
    </IonPage>
  );
}

/**
 * Render the receipt data as a simple printable document and invoke the
 * browser's print dialog (Req 8.5 — "printable receipt"). A dedicated print
 * window keeps the receipt layout isolated from the app shell.
 */
function openPrintableReceipt(receipt: ReceiptResponse): void {
  const currency = receipt.currency || "₹";
  const money = (v: number) => `${currency}${Number(v ?? 0).toFixed(2)}`;
  const rows = receipt.items
    .map(
      (it) =>
        `<tr><td>${escapeHtml(it.name)}</td><td style="text-align:right">${it.quantity}</td>` +
        `<td style="text-align:right">${money(it.unit_price)}</td>` +
        `<td style="text-align:right">${money(it.line_total)}</td></tr>`
    )
    .join("");

  const html =
    `<!doctype html><html><head><meta charset="utf-8"><title>` +
    `Receipt ${escapeHtml(receipt.receipt_number)}</title>` +
    `<style>body{font-family:system-ui,sans-serif;padding:16px;max-width:320px}` +
    `h1{font-size:18px;margin:0 0 4px}table{width:100%;border-collapse:collapse;font-size:13px}` +
    `th,td{padding:2px 0}tfoot td{border-top:1px solid #000;font-weight:bold}` +
    `.muted{color:#555;font-size:12px}</style></head><body>` +
    `<h1>${escapeHtml(receipt.business_name)}</h1>` +
    `<div class="muted">Receipt ${escapeHtml(receipt.receipt_number)}` +
    (receipt.date ? ` · ${escapeHtml(receipt.date)}` : "") +
    `</div><table><thead><tr><th style="text-align:left">Item</th>` +
    `<th style="text-align:right">Qty</th><th style="text-align:right">Price</th>` +
    `<th style="text-align:right">Total</th></tr></thead><tbody>${rows}</tbody>` +
    `<tfoot><tr><td colspan="3">Subtotal</td><td style="text-align:right">${money(
      receipt.subtotal
    )}</td></tr>` +
    `<tr><td colspan="3">Total</td><td style="text-align:right">${money(
      receipt.total_amount
    )}</td></tr></tfoot></table>` +
    (receipt.payment_method
      ? `<p class="muted">Paid via ${escapeHtml(receipt.payment_method)}` +
        (receipt.payment_status ? ` (${escapeHtml(receipt.payment_status)})` : "") +
        `</p>`
      : "") +
    `<script>window.onload=function(){window.print();}</script></body></html>`;

  const printWindow = window.open("", "_blank", "width=380,height=600");
  if (!printWindow) {
    // Popup blocked — surface via throw so the caller reports it.
    throw new Error("Please allow pop-ups to print the receipt.");
  }
  printWindow.document.open();
  printWindow.document.write(html);
  printWindow.document.close();
}

/**
 * Decode a base64-encoded PDF and trigger a browser download (Req 8.5 —
 * "downloadable invoice").
 */
function downloadBase64Pdf(base64: string, filename: string): void {
  const byteChars = atob(base64);
  const byteNumbers = new Array<number>(byteChars.length);
  for (let i = 0; i < byteChars.length; i++) {
    byteNumbers[i] = byteChars.charCodeAt(i);
  }
  const blob = new Blob([new Uint8Array(byteNumbers)], { type: "application/pdf" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
  // Revoke on the next tick so the download has a chance to start.
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** Minimal HTML-escaping for values interpolated into the print document. */
function escapeHtml(value: string): string {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// =============================================================================
// Owner "Add item" — create a single product/variant (Req 8.1)
// =============================================================================

/** Coerce an editable string field to a number, or `undefined` when blank/invalid. */
function numOrUndefined(value: string): number | undefined {
  const t = value.trim();
  if (t === "") return undefined;
  const n = Number(t);
  return Number.isFinite(n) ? n : undefined;
}

/** Coerce an editable string field to a trimmed string, or `undefined` when blank. */
function strOrUndefined(value: string): string | undefined {
  const t = value.trim();
  return t === "" ? undefined : t;
}

/**
 * The "Add item" modal form. Creates a product via `sell.createProduct`; on
 * success the parent closes the modal and reloads the session so the new item
 * shows in the grid. Failures surface inline.
 */
function AddItemForm({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: () => void;
}): JSX.Element {
  const [name, setName] = useState<string>("");
  const [category, setCategory] = useState<string>("");
  const [sizeLabel, setSizeLabel] = useState<string>("standard");
  const [price, setPrice] = useState<string>("");
  const [busy, setBusy] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  const submit = useCallback(async (): Promise<void> => {
    const trimmedName = name.trim();
    const priceNum = numOrUndefined(price);
    if (!trimmedName) {
      setError("Enter a product name.");
      return;
    }
    if (priceNum === undefined || priceNum <= 0) {
      setError("Enter a price greater than 0.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await sell.createProduct({
        name: trimmedName,
        category: strOrUndefined(category),
        size_label: strOrUndefined(sizeLabel) ?? "standard",
        price: priceNum,
      });
      onCreated();
    } catch (err) {
      setError(messageFor(err, "Couldn't add the item. Please try again."));
    } finally {
      setBusy(false);
    }
  }, [name, category, sizeLabel, price, onCreated]);

  return (
    <>
      <IonHeader>
        <IonToolbar>
          <IonTitle>Add item</IonTitle>
          <IonButtons slot="end">
            <IonButton onClick={onClose}>Cancel</IonButton>
          </IonButtons>
        </IonToolbar>
      </IonHeader>
      <IonContent className="ion-padding">
        <IonList>
          <IonItem>
            <IonInput
              label="Product name"
              labelPlacement="stacked"
              value={name}
              onIonInput={(e) => setName(e.detail.value ?? "")}
            />
          </IonItem>
          <IonItem>
            <IonInput
              label="Category (optional)"
              labelPlacement="stacked"
              value={category}
              onIonInput={(e) => setCategory(e.detail.value ?? "")}
            />
          </IonItem>
          <IonItem>
            <IonInput
              label="Size / label"
              labelPlacement="stacked"
              value={sizeLabel}
              onIonInput={(e) => setSizeLabel(e.detail.value ?? "")}
            />
          </IonItem>
          <IonItem>
            <IonInput
              label="Price"
              labelPlacement="stacked"
              type="number"
              inputmode="decimal"
              value={price}
              onIonInput={(e) => setPrice(e.detail.value ?? "")}
            />
          </IonItem>
        </IonList>

        {error && (
          <IonNote color="danger" style={{ display: "block", margin: "8px 0" }}>
            {error}
          </IonNote>
        )}

        <IonButton expand="block" disabled={busy} onClick={() => void submit()}>
          {busy ? <IonSpinner name="dots" /> : "Add item"}
        </IonButton>
      </IonContent>
    </>
  );
}

// =============================================================================
// Owner "Upload catalog" — scan → edit → confirm (Req 8.1, 15.6)
// =============================================================================

/** Read an unknown record value as a display string (blank when absent). */
function asStr(record: Record<string, unknown>, key: string): string {
  const v = record[key];
  if (v === undefined || v === null) return "";
  return String(v);
}

/** Read an array of records from an unknown record (empty when absent/typed wrong). */
function asRecords(record: Record<string, unknown>, key: string): Record<string, unknown>[] {
  const v = record[key];
  if (!Array.isArray(v)) return [];
  return v.filter((el): el is Record<string, unknown> => typeof el === "object" && el !== null);
}

interface VariantState {
  size_label: string;
  price: string;
}

interface ProductDraftState {
  name: string;
  variants: VariantState[];
}

interface CategoryState {
  name: string;
  products: ProductDraftState[];
}

/** Build the editable category tree from an extracted catalog draft. */
function categoriesFrom(draft: Record<string, unknown>): CategoryState[] {
  return asRecords(draft, "categories").map((cat) => ({
    name: asStr(cat, "name"),
    products: asRecords(cat, "products").map((p) => ({
      name: asStr(p, "name"),
      variants: asRecords(p, "variants").map((v) => ({
        size_label: asStr(v, "size_label"),
        price: asStr(v, "price"),
      })),
    })),
  }));
}

/**
 * The "Upload catalog" modal: pick a photo → extract → edit the nested
 * categories→products→variants draft → confirm. On a successful confirm the
 * parent closes and reloads the session. Extraction/confirm failures surface
 * inline; discard is client-side only (drop the draft, no server call).
 */
function UploadCatalogModal({
  onClose,
  onImported,
}: {
  onClose: () => void;
  onImported: () => void;
}): JSX.Element {
  const [file, setFile] = useState<File | null>(null);
  const [extracting, setExtracting] = useState<boolean>(false);
  const [extractError, setExtractError] = useState<string | null>(null);
  const [draft, setDraft] = useState<Record<string, unknown> | null>(null);

  const onPickFile = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    setFile(e.target.files?.[0] ?? null);
    setExtractError(null);
  }, []);

  const extract = useCallback(async (): Promise<void> => {
    if (!file) {
      setExtractError("Choose an image of your catalog to scan.");
      return;
    }
    setExtracting(true);
    setExtractError(null);
    try {
      const res = await ingestion.extract("catalog", file);
      setDraft(res.draft ?? {});
    } catch (err) {
      setDraft(null);
      setExtractError(messageFor(err, "We couldn't read that image. Please try another."));
    } finally {
      setExtracting(false);
    }
  }, [file]);

  // Discard is client-side only — drop the draft, return to the upload step.
  const discard = useCallback((): void => {
    setDraft(null);
    setFile(null);
    setExtractError(null);
  }, []);

  return (
    <>
      <IonHeader>
        <IonToolbar>
          <IonTitle>Upload catalog</IonTitle>
          <IonButtons slot="end">
            <IonButton onClick={onClose}>Cancel</IonButton>
          </IonButtons>
        </IonToolbar>
      </IonHeader>
      <IonContent className="ion-padding">
        {draft === null ? (
          <>
            <IonItem>
              <IonLabel position="stacked">Catalog photo</IonLabel>
              <input
                type="file"
                accept="image/*"
                capture="environment"
                onChange={onPickFile}
                style={{ marginTop: 8 }}
              />
            </IonItem>
            {extractError && (
              <IonNote color="danger" style={{ display: "block", margin: "8px 0" }}>
                {extractError}
              </IonNote>
            )}
            <IonButton
              expand="block"
              disabled={extracting || !file}
              onClick={() => void extract()}
            >
              {extracting ? <IonSpinner name="dots" /> : "Scan catalog"}
            </IonButton>
          </>
        ) : (
          <CatalogConfirmForm draft={draft} onImported={onImported} onDiscard={discard} />
        )}
      </IonContent>
    </>
  );
}

/**
 * Editable confirm form for a scanned catalog draft (adapted from Ingestion's
 * `CatalogDraftForm`). Categories → products → priced variants are all editable
 * (add/remove). Confirm persists via `ingestion.confirm({doc_type:"catalog"})`.
 */
function CatalogConfirmForm({
  draft,
  onImported,
  onDiscard,
}: {
  draft: Record<string, unknown>;
  onImported: () => void;
  onDiscard: () => void;
}): JSX.Element {
  const [categories, setCategories] = useState<CategoryState[]>(() => categoriesFrom(draft));
  const [busy, setBusy] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  const updateCategory = (ci: number, patch: Partial<CategoryState>) =>
    setCategories((prev) => prev.map((c, i) => (i === ci ? { ...c, ...patch } : c)));
  const addCategory = () =>
    setCategories((prev) => [
      ...prev,
      { name: "", products: [{ name: "", variants: [{ size_label: "standard", price: "" }] }] },
    ]);
  const removeCategory = (ci: number) =>
    setCategories((prev) => prev.filter((_, i) => i !== ci));

  const updateProduct = (ci: number, pi: number, patch: Partial<ProductDraftState>) =>
    updateCategory(ci, {
      products: categories[ci].products.map((p, i) => (i === pi ? { ...p, ...patch } : p)),
    });
  const addProduct = (ci: number) =>
    updateCategory(ci, {
      products: [
        ...categories[ci].products,
        { name: "", variants: [{ size_label: "standard", price: "" }] },
      ],
    });
  const removeProduct = (ci: number, pi: number) =>
    updateCategory(ci, { products: categories[ci].products.filter((_, i) => i !== pi) });

  const updateVariant = (ci: number, pi: number, vi: number, patch: Partial<VariantState>) =>
    updateProduct(ci, pi, {
      variants: categories[ci].products[pi].variants.map((v, i) =>
        i === vi ? { ...v, ...patch } : v
      ),
    });
  const addVariant = (ci: number, pi: number) =>
    updateProduct(ci, pi, {
      variants: [...categories[ci].products[pi].variants, { size_label: "standard", price: "" }],
    });
  const removeVariant = (ci: number, pi: number, vi: number) =>
    updateProduct(ci, pi, {
      variants: categories[ci].products[pi].variants.filter((_, i) => i !== vi),
    });

  const submit = useCallback(async (): Promise<void> => {
    const payload = {
      categories: categories.map((c) => ({
        name: strOrUndefined(c.name),
        products: c.products.map((p) => ({
          name: strOrUndefined(p.name),
          variants: p.variants.map((v) => ({
            size_label: strOrUndefined(v.size_label),
            price: numOrUndefined(v.price),
          })),
        })),
      })),
    };
    setBusy(true);
    setError(null);
    try {
      await ingestion.confirm({ doc_type: "catalog", draft: payload });
      onImported();
    } catch (err) {
      setError(messageFor(err, "We couldn't save these products. Please review and try again."));
    } finally {
      setBusy(false);
    }
  }, [categories, onImported]);

  return (
    <>
      <IonItem lines="none">
        <IonLabel>
          <h2>Review catalog</h2>
          <IonNote color="medium">Edit any field, then confirm or discard.</IonNote>
        </IonLabel>
      </IonItem>

      {categories.length === 0 ? (
        <IonItem lines="none">
          <IonText color="medium">No categories detected. Add one to continue.</IonText>
        </IonItem>
      ) : null}

      {categories.map((cat, ci) => (
        <IonList key={ci}>
          <IonListHeader>
            <IonLabel>Category {ci + 1}</IonLabel>
            <IonButton size="small" fill="clear" color="danger" onClick={() => removeCategory(ci)}>
              Remove
            </IonButton>
          </IonListHeader>
          <IonItem>
            <IonInput
              label="Category name"
              labelPlacement="stacked"
              value={cat.name}
              onIonInput={(e) => updateCategory(ci, { name: e.detail.value ?? "" })}
            />
          </IonItem>

          {cat.products.map((product, pi) => (
            <IonItemGroup key={pi}>
              <IonItemDivider>
                <IonLabel>Product {pi + 1}</IonLabel>
                <IonButton
                  slot="end"
                  fill="clear"
                  size="small"
                  color="danger"
                  onClick={() => removeProduct(ci, pi)}
                >
                  Remove
                </IonButton>
              </IonItemDivider>
              <IonItem>
                <IonInput
                  label="Product name"
                  labelPlacement="stacked"
                  value={product.name}
                  onIonInput={(e) => updateProduct(ci, pi, { name: e.detail.value ?? "" })}
                />
              </IonItem>
              {product.variants.map((variant, vi) => (
                <IonItem key={vi}>
                  <IonInput
                    label="Size"
                    labelPlacement="stacked"
                    value={variant.size_label}
                    onIonInput={(e) =>
                      updateVariant(ci, pi, vi, { size_label: e.detail.value ?? "" })
                    }
                  />
                  <IonInput
                    label="Price"
                    labelPlacement="stacked"
                    type="number"
                    inputmode="decimal"
                    value={variant.price}
                    onIonInput={(e) => updateVariant(ci, pi, vi, { price: e.detail.value ?? "" })}
                  />
                  <IonButton
                    slot="end"
                    fill="clear"
                    size="small"
                    color="danger"
                    onClick={() => removeVariant(ci, pi, vi)}
                  >
                    ✕
                  </IonButton>
                </IonItem>
              ))}
              <IonButton size="small" fill="clear" onClick={() => addVariant(ci, pi)}>
                Add variant
              </IonButton>
            </IonItemGroup>
          ))}

          <IonButton size="small" fill="clear" onClick={() => addProduct(ci)}>
            Add product
          </IonButton>
        </IonList>
      ))}

      <IonButton size="small" onClick={addCategory} style={{ marginTop: 8 }}>
        Add category
      </IonButton>

      {error && (
        <IonNote color="danger" style={{ display: "block", margin: "8px 0" }}>
          {error}
        </IonNote>
      )}

      <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
        <IonButton expand="block" style={{ flex: 1 }} disabled={busy} onClick={() => void submit()}>
          {busy ? <IonSpinner name="dots" /> : "Confirm"}
        </IonButton>
        <IonButton
          expand="block"
          style={{ flex: 1 }}
          fill="outline"
          color="medium"
          disabled={busy}
          onClick={onDiscard}
        >
          Discard
        </IonButton>
      </div>
    </>
  );
}

export default Sell;
