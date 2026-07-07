/**
 * Invoices — the Invoices_Surface (design §"Frontend structure" → `tabs/Invoices.tsx`).
 *
 * A thin surface over the `/api/v1/invoices` endpoint (`invoices.generate`),
 * which itself is a thin HTTP adapter over the unchanged `InvoiceService`
 * (design §"No business-logic rewrite"). The flow is:
 *
 *  1. **Select an existing order.** The tab lists the tenant's orders
 *     (`orders.list`) and lets the user pick one to invoice.
 *  2. **Request an invoice (Req 13.1).** Submitting posts the selected order id
 *     (plus an optional GST rate/label) to `POST /api/v1/invoices`. The
 *     Service_Layer assembles the invoice — business name, customer details,
 *     an itemised list with quantities and unit prices, a subtotal, applicable
 *     taxes, and the total amount due — and renders it to a PDF, returned
 *     base64-encoded alongside the serialized invoice metadata.
 *  3. **Display + download the generated PDF (Req 13.1, 13.3).** The returned
 *     invoice metadata is rendered as a preview and the base64 PDF can be
 *     downloaded. Every monetary value is shown in the currency configured for
 *     the Tenant (`invoice.currency`, resolved server-side), so the surface
 *     never substitutes its own currency (Req 13.3).
 *
 * The base64-PDF download reuses the same object-URL approach as the Sell
 * surface's downloadable invoice (`tabs/Sell.tsx`).
 */

import { useCallback, useState } from "react";
import {
  useIonViewWillEnter,
  IonButton,
  IonCard,
  IonCardContent,
  IonCardHeader,
  IonCardSubtitle,
  IonCardTitle,
  IonContent,
  IonHeader,
  IonInput,
  IonItem,
  IonLabel,
  IonList,
  IonNote,
  IonPage,
  IonSelect,
  IonSelectOption,
  IonSpinner,
  IonText,
  IonTitle,
  IonToolbar,
} from "@ionic/react";
import { TabTour } from "../components/TabTour";
import {
  ApiError,
  invoices as invoicesApi,
  orders as ordersApi,
  type InvoiceData,
  type InvoiceGenerateBody,
  type Money,
  type Order,
} from "../api/endpoints";

export function Invoices(): JSX.Element {
  // ── Orders to choose from (Req 13.1 — "an existing order") ──
  const [orders, setOrders] = useState<Order[]>([]);
  const [ordersLoading, setOrdersLoading] = useState<boolean>(false);
  const [ordersError, setOrdersError] = useState<string | null>(null);
  const [selectedOrderId, setSelectedOrderId] = useState<string | null>(null);

  // ── Optional GST configuration for this invoice (Req 13.2, 13.7) ──
  const [taxRate, setTaxRate] = useState<string>("");
  const [taxLabel, setTaxLabel] = useState<string>("");

  // ── Generation state + the resulting invoice (Req 13.1, 13.3) ──
  const [generating, setGenerating] = useState<boolean>(false);
  const [generateError, setGenerateError] = useState<string | null>(null);
  const [invoice, setInvoice] = useState<InvoiceData | null>(null);
  const [pdfBase64, setPdfBase64] = useState<string | null>(null);
  const [pdfFilename, setPdfFilename] = useState<string>("invoice.pdf");

  const loadOrders = useCallback(async (): Promise<void> => {
    setOrdersLoading(true);
    setOrdersError(null);
    try {
      const rows = await ordersApi.list();
      setOrders(rows);
    } catch (err) {
      setOrdersError(messageFor(err, "Could not load orders."));
    } finally {
      setOrdersLoading(false);
    }
  }, []);

  // Ionic keeps tab components mounted, so a mount-only effect would leave this
  // list stale. Reload the orders every time the Invoices tab becomes active so
  // newly created orders appear immediately.
  useIonViewWillEnter(() => {
    void loadOrders();
  });

  const handleGenerate = useCallback(async (): Promise<void> => {
    setGenerateError(null);
    if (!selectedOrderId) {
      setGenerateError("Select an order to invoice.");
      return;
    }

    const body: InvoiceGenerateBody = { order_id: selectedOrderId };
    const rate = taxRate.trim();
    if (rate) body.tax_rate = rate;
    const label = taxLabel.trim();
    if (label) body.tax_label = label;

    setGenerating(true);
    try {
      // POST /invoices — the Service_Layer generates the PDF (Req 13.1). The
      // response carries the serialized invoice metadata and the base64 PDF.
      const res = await invoicesApi.generate(body);
      setInvoice(res.invoice ?? null);
      setPdfBase64(res.data);
      setPdfFilename(res.filename || `invoice-${selectedOrderId}.pdf`);
    } catch (err) {
      // Order-not-found (Req 13.5) and future-delivery-date (Req 13.6) rejections
      // surface here with the server's message; no PDF is produced.
      setInvoice(null);
      setPdfBase64(null);
      setGenerateError(messageFor(err, "The invoice could not be generated."));
    } finally {
      setGenerating(false);
    }
  }, [selectedOrderId, taxRate, taxLabel]);

  const handleDownload = useCallback((): void => {
    if (!pdfBase64) return;
    downloadBase64Pdf(pdfBase64, pdfFilename);
  }, [pdfBase64, pdfFilename]);

  return (
    <IonPage>
      <TabTour
        tabKey="invoices"
        title="Invoices"
        intro="Generate professional invoices."
        points={[
          "Pick an existing order to invoice.",
          "Add GST if needed, then download the PDF to share.",
        ]}
      />
      <IonHeader>
        <IonToolbar>
          <IonTitle>Invoices</IonTitle>
        </IonToolbar>
      </IonHeader>

      <IonContent className="ion-padding">
        {/* ── Choose an order + optional GST, then generate ── */}
        <IonList>
          <IonItem>
            <IonSelect
              label="Order"
              labelPlacement="stacked"
              value={selectedOrderId}
              placeholder={
                ordersLoading ? "Loading orders…" : "Select an order to invoice"
              }
              disabled={ordersLoading || orders.length === 0}
              onIonChange={(e) => setSelectedOrderId((e.detail.value as string) ?? null)}
              interface="popover"
            >
              {orders.map((order) => (
                <IonSelectOption key={order.order_id} value={order.order_id}>
                  {orderLabel(order)}
                </IonSelectOption>
              ))}
            </IonSelect>
          </IonItem>

          <IonItem>
            <IonInput
              label="Tax rate % (optional)"
              labelPlacement="stacked"
              type="number"
              inputmode="decimal"
              min={0}
              max={100}
              value={taxRate}
              onIonInput={(e) => setTaxRate(e.detail.value ?? "")}
              placeholder="e.g. 18"
            />
          </IonItem>

          <IonItem>
            <IonInput
              label="Tax label (optional)"
              labelPlacement="stacked"
              value={taxLabel}
              onIonInput={(e) => setTaxLabel(e.detail.value ?? "")}
              placeholder="e.g. GST"
            />
          </IonItem>
        </IonList>

        {ordersError ? (
          <IonNote color="danger" style={{ display: "block", margin: "8px 0" }}>
            {ordersError}{" "}
            <IonButton fill="clear" size="small" onClick={() => void loadOrders()}>
              Retry
            </IonButton>
          </IonNote>
        ) : null}

        {!ordersLoading && !ordersError && orders.length === 0 ? (
          <IonText color="medium">
            <p style={{ textAlign: "center", marginTop: 24 }}>
              No orders are available to invoice.
            </p>
          </IonText>
        ) : null}

        <IonButton
          expand="block"
          disabled={generating || !selectedOrderId}
          onClick={() => void handleGenerate()}
        >
          {generating ? <IonSpinner name="dots" /> : "Generate invoice"}
        </IonButton>

        {generateError ? (
          <IonNote color="danger" style={{ display: "block", margin: "8px 0" }}>
            {generateError}
          </IonNote>
        ) : null}

        {/* ── Generated invoice preview + download (Req 13.1, 13.3) ── */}
        {invoice ? (
          <InvoicePreview
            invoice={invoice}
            onDownload={handleDownload}
            canDownload={pdfBase64 !== null}
          />
        ) : null}
      </IonContent>
    </IonPage>
  );
}

interface InvoicePreviewProps {
  invoice: InvoiceData;
  onDownload: () => void;
  canDownload: boolean;
}

/**
 * Render the generated invoice's metadata as a preview. Every monetary value is
 * formatted with the tenant's configured currency carried on the invoice
 * (Req 13.3), never a hard-coded symbol.
 */
function InvoicePreview({ invoice, onDownload, canDownload }: InvoicePreviewProps): JSX.Element {
  const currency = invoice.currency ?? "";
  const money = (v: Money) => formatMoney(v, currency);
  const showTax = Number(invoice.tax_rate) > 0 || Number(invoice.tax_amount) > 0;

  return (
    <IonCard>
      <IonCardHeader>
        <IonCardSubtitle>
          Issued {invoice.issue_date} · Delivery {invoice.delivery_date}
        </IonCardSubtitle>
        <IonCardTitle style={{ fontSize: "1rem" }}>
          Invoice {invoice.invoice_number}
        </IonCardTitle>
      </IonCardHeader>
      <IonCardContent>
        <IonList>
          {invoice.items.map((item, index) => (
            <IonItem key={index} lines="none">
              <IonLabel>
                <h3>
                  {item.description} × {item.quantity}
                </h3>
                <IonNote>
                  {money(item.unit_price)} each — {money(item.total)}
                </IonNote>
              </IonLabel>
            </IonItem>
          ))}
        </IonList>

        <div style={{ marginTop: 12 }}>
          <SummaryRow label="Subtotal" value={money(invoice.subtotal)} />
          {showTax ? (
            <SummaryRow
              label={`${invoice.tax_label || "Tax"} (${formatRate(invoice.tax_rate)}%)`}
              value={money(invoice.tax_amount)}
            />
          ) : null}
          {Number(invoice.amount_paid) > 0 ? (
            <SummaryRow label="Amount paid" value={money(invoice.amount_paid)} />
          ) : null}
          <SummaryRow label="Amount due" value={money(invoice.amount_due)} strong />
        </div>

        <IonButton
          expand="block"
          fill="outline"
          style={{ marginTop: 12 }}
          disabled={!canDownload}
          onClick={onDownload}
        >
          Download PDF
        </IonButton>
      </IonCardContent>
    </IonCard>
  );
}

interface SummaryRowProps {
  label: string;
  value: string;
  strong?: boolean;
}

/** A single label/value line in the invoice totals summary. */
function SummaryRow({ label, value, strong }: SummaryRowProps): JSX.Element {
  return (
    <div
      style={{
        display: "flex",
        justifyContent: "space-between",
        padding: "2px 0",
        fontWeight: strong ? 700 : 400,
      }}
    >
      <span>{label}</span>
      <span>{value}</span>
    </div>
  );
}

/**
 * A readable label for an order in the select list. Leads with the customer
 * name (Req 13.6) so the Owner recognises the order, falling back to a short id
 * only when the name is unavailable, then the delivery date and item summary.
 */
function orderLabel(order: Order): string {
  const item = order.items[0];
  const summary = item
    ? `${item.recipe_name}${order.items.length > 1 ? ` +${order.items.length - 1}` : ""}`
    : "No items";
  const who = order.customer_name?.trim() || `Order ${shortId(order.order_id)}`;
  return `${who} · ${order.delivery_date} · ${summary}`;
}

/**
 * Format a monetary value in the tenant's configured currency (Req 13.3). The
 * `currency` is the tenant currency prefix resolved server-side (e.g. "₹",
 * "Rs.", "$"); it is used verbatim so the surface never re-derives a symbol.
 */
function formatMoney(value: Money, currency: string): string {
  const n = Number(value);
  const amount = Number.isNaN(n) ? String(value) : n.toFixed(2);
  const prefix = currency ? `${currency}${currency.endsWith(".") ? " " : ""}` : "";
  return `${prefix}${amount}`;
}

/** Format a tax rate for display, trimming trailing zeros. */
function formatRate(value: Money): string {
  const n = Number(value);
  if (Number.isNaN(n)) return String(value);
  return String(Number(n.toFixed(2)));
}

/** A short, readable fragment of a UUID for labels. */
function shortId(id: string): string {
  return id.slice(0, 8);
}

/** Extract a user-facing message from an unknown thrown value. */
function messageFor(err: unknown, fallback: string): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error && err.message) return err.message;
  return fallback;
}

/**
 * Decode a base64-encoded PDF and trigger a browser download. Mirrors the
 * downloadable-invoice approach used on the Sell surface (`tabs/Sell.tsx`).
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

export default Invoices;
