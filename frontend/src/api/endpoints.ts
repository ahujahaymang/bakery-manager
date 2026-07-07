/**
 * Typed per-domain API calls (Req 20.2).
 *
 * One function per implemented `/api/v1/...` route, grouped by domain, all
 * routed through the shared {@link http} wrapper in `client.ts` (30s timeout,
 * device-token attachment, typed error surfacing). Request/response types
 * mirror the backend router + `app/api/schemas.py` shapes.
 *
 * Monetary values are typed as {@link Money} (`number | string`) because the
 * backend serializes `Decimal` fields as numbers on the hand-built dict
 * responses (Sell/receipt) and may render them as strings on the
 * schema-serialized responses; callers should coerce with `Number(...)` before
 * arithmetic.
 */

import { http, type ApiRequestOptions } from "./client";

/** A monetary/decimal value as delivered by the backend JSON. */
export type Money = number | string;

/** ISO-8601 date (`YYYY-MM-DD`) or date-time string. */
export type IsoDate = string;

/** A UUID string. */
export type Uuid = string;

// =============================================================================
// Auth  (/api/v1/auth) — OTP, PIN, WebAuthn, users, Manage_Mode elevation
// =============================================================================

export type UserRole = "owner" | "staff";

export interface RequestOtpBody {
  phone: string;
}

export interface RequestOtpResponse {
  /** The stored challenge id. */
  challenge_id: Uuid;
  phone: string;
  /** The channel the OTP was delivered over (telegram/whatsapp/sms), if any. */
  delivery_channel?: string | null;
  expires_at?: IsoDate;
}

export interface VerifyOtpBody {
  phone: string;
  code: string;
  /** Optional device metadata (label/platform) recorded with the session. */
  device_info?: Record<string, unknown>;
}

export interface AuthedUserResponse {
  user_id: Uuid;
  tenant_id: Uuid;
  name: string;
  phone: string;
  role: UserRole;
}

export interface DeviceSessionResponse {
  /** The raw device session token, returned exactly once (Bearer fallback). */
  token: string;
  expires_at: IsoDate;
  user: AuthedUserResponse;
}

export interface SetPinBody {
  pin: string;
}

export interface VerifyPinBody {
  pin: string;
}

export interface VerifyPinResponse {
  verified: boolean;
}

export interface CreateUserBody {
  name: string;
  phone: string;
  role: UserRole;
}

export interface ElevateBody {
  /** The user whose Owner credential is being verified. */
  user_id?: Uuid;
  /** PIN or a WebAuthn assertion payload proving Owner identity. */
  pin?: string;
  webauthn?: Record<string, unknown>;
  /** "manage" (default) to elevate, or "switch" to change active user. */
  purpose?: string;
}

export interface ElevateResponseFull {
  elevated: boolean;
  mode?: string;
}

export interface ElevateResponse {
  elevated: boolean;
}

export interface SessionResponse {
  user: AuthedUserResponse;
}

export const auth = {
  /**
   * GET /auth/session — resolve the current user from the device's existing
   * token so the App can reopen without re-verifying an OTP (Req 2.10). Throws
   * a 401 ApiError when the token is missing/expired (caller signs out).
   */
  session: () => http.get<SessionResponse>("/auth/session"),

  /** POST /auth/otp/request — request an OTP for a phone number (Req 2.1, 2.2). */
  requestOtp: (body: RequestOtpBody) =>
    http.post<RequestOtpResponse>("/auth/otp/request", { json: body }),

  /** POST /auth/otp/verify — verify an OTP and mint a device session (Req 2.5). */
  verifyOtp: (body: VerifyOtpBody) =>
    http.post<DeviceSessionResponse>("/auth/otp/verify", { json: body }),

  /** POST /auth/pin — set the current user's PIN (Req 4.1). */
  setPin: (body: SetPinBody) => http.post<void>("/auth/pin", { json: body, responseType: "void" }),

  /** POST /auth/pin/verify — verify the current user's PIN (Req 4.3). */
  verifyPin: (body: VerifyPinBody) =>
    http.post<VerifyPinResponse>("/auth/pin/verify", { json: body }),

  /** GET /auth/users — list tenant users (Owner only, Req 5.5). */
  listUsers: () => http.get<AuthedUserResponse[]>("/auth/users"),

  /** POST /auth/users — create a user (Owner only, Req 5.5, 5.7). */
  createUser: (body: CreateUserBody) =>
    http.post<AuthedUserResponse>("/auth/users", { json: body }),

  /** POST /auth/mode/manage — elevate to Manage_Mode via PIN/WebAuthn (Req 6.3). */
  elevate: (body: ElevateBody) =>
    http.post<ElevateResponse>("/auth/mode/manage", { json: body }),
};

// =============================================================================
// Sell / Point-of-Sale  (/api/v1/sell)
// =============================================================================

export interface SellSessionItem {
  variant_id: Uuid;
  product_name: string;
  variant_label: string;
  unit_price: number;
  /** `null` for unlimited stock (the app-first always-on Sell session). */
  stock_qty: number | null;
  sold_qty: number;
  /** `null` for unlimited stock. */
  remaining: number | null;
}

export interface SellSession {
  session_id: Uuid;
  name: string;
  mode: string;
  started_at: IsoDate | null;
  ends_at: IsoDate | null;
  items: SellSessionItem[];
}

export interface SellSessionResponse {
  session: SellSession | null;
}

export interface SellCheckoutItem {
  variant_id: string;
  quantity: number;
  extra_charge?: Money;
  extra_note?: string | null;
}

/** Body for the Owner "Add item" form → `POST /sell/products`. */
export interface SellProductCreateBody {
  name: string;
  category?: string;
  /** Defaults to "standard" server-side when omitted. */
  size_label?: string;
  price: number;
}

export interface SellProductVariant {
  variant_id: Uuid;
  size_label: string;
  price: number;
}

/** Response from `POST /sell/products` — the created product + its variants. */
export interface SellProductResponse {
  product_id: Uuid;
  name: string;
  category: string | null;
  variants: SellProductVariant[];
}

export interface SellCheckoutBody {
  items: SellCheckoutItem[];
  payment_method: "cash" | "upi";
  session_id?: string | null;
  customer_name?: string | null;
  gst_rate?: Money;
}

export interface SaleLineItem {
  recipe_name: string;
  quantity: number;
  unit_price: number;
  customization_charge: number;
  customization_note: string;
  line_total: number;
}

export interface SaleResponse {
  order_id: Uuid;
  created_by_user_id: Uuid | null;
  status: string;
  subtotal: number;
  total_amount: number;
  payment_id: Uuid | null;
  payment_method: string | null;
  payment_status: string | null;
  items: SaleLineItem[];
  idempotent_replay: boolean;
}

export interface ReceiptLineItem {
  name: string;
  quantity: number;
  unit_price: number;
  line_total: number;
}

export interface ReceiptResponse {
  order_id: Uuid;
  receipt_number: string;
  business_name: string;
  currency: string;
  date: IsoDate | null;
  items: ReceiptLineItem[];
  subtotal: number;
  total_amount: number;
  payment_method: string | null;
  payment_status: string | null;
}

/** Base64-encoded PDF payload returned by the invoice endpoints. */
export interface InvoicePdfResponse {
  filename: string;
  content_type: string;
  /** Base64-encoded PDF bytes. */
  data: string;
  /** Present on the invoices-router response; absent on the sell-router one. */
  invoice?: InvoiceData;
}

export const sell = {
  /** GET /sell/session — active session + items for sale (Req 8.1). */
  getSession: () => http.get<SellSessionResponse>("/sell/session"),

  /**
   * POST /sell/products — Owner "Add item": create a product and sync it into
   * the always-on Sell session so it appears in the grid (Req 8.1). Owner-only.
   */
  createProduct: (body: SellProductCreateBody) =>
    http.post<SellProductResponse>("/sell/products", { json: body }),

  /**
   * POST /sell/checkout — create one order + payment (Req 7, 8.3–8.8).
   * Pass `idempotencyKey` to make offline replays persist at most once (Req 18.5).
   */
  checkout: (body: SellCheckoutBody, idempotencyKey?: string) =>
    http.post<SaleResponse>("/sell/checkout", {
      json: body,
      headers: idempotencyKey ? { "Idempotency-Key": idempotencyKey } : undefined,
    }),

  /** GET /sell/receipt/{orderId} — printable receipt data (Req 8.5). */
  getReceipt: (orderId: Uuid) =>
    http.get<ReceiptResponse>(`/sell/receipt/${encodeURIComponent(orderId)}`),

  /** GET /sell/invoice/{orderId} — downloadable base64 PDF invoice (Req 8.5). */
  getInvoice: (orderId: Uuid, taxRate?: number) =>
    http.get<InvoicePdfResponse>(`/sell/invoice/${encodeURIComponent(orderId)}`, {
      query: { tax_rate: taxRate },
    }),
};

// =============================================================================
// Orders  (/api/v1/orders)
// =============================================================================

export type OrderStatus = "pending" | "delivered" | "cancelled";

export interface OrderItemCreate {
  recipe_name: string;
  quantity: number;
  selling_price: Money;
  customization_charge?: Money;
  customization_note?: string | null;
}

export interface OrderCreateBody {
  customer_identifier: string;
  delivery_date: IsoDate;
  items: OrderItemCreate[];
  delivery_address?: string | null;
}

export interface OrderItem {
  order_item_id: Uuid;
  recipe_id: Uuid | null;
  recipe_name: string;
  quantity: number;
  selling_price: Money;
  customization_charge: Money;
  customization_note: string | null;
}

export interface Order {
  order_id: Uuid;
  tenant_id: Uuid;
  customer_id: Uuid;
  customer_name: string | null;
  delivery_date: IsoDate;
  delivery_address: string | null;
  status: OrderStatus;
  booth_session_id: Uuid | null;
  created_by_user_id: Uuid | null;
  created_at: IsoDate | null;
  updated_at: IsoDate | null;
  items: OrderItem[];
}

export interface OrderListQuery {
  status?: OrderStatus;
  delivery_date?: IsoDate;
  date_from?: IsoDate;
  date_to?: IsoDate;
  /** Index signature so this satisfies the client's query-params type. */
  [key: string]: string | number | boolean | null | undefined;
}

export const orders = {
  /** POST /orders — create a pending order (Req 9.1). */
  create: (body: OrderCreateBody) => http.post<Order>("/orders", { json: body }),

  /** GET /orders — list, optionally filtered by status/date (Req 9.2, 9.5). */
  list: (query?: OrderListQuery) => http.get<Order[]>("/orders", { query }),

  /** POST /orders/{id}/deliver — mark a pending order delivered (Req 9.3). */
  deliver: (orderId: Uuid) =>
    http.post<Order>(`/orders/${encodeURIComponent(orderId)}/deliver`),

  /** POST /orders/{id}/cancel — cancel a pending order, retain record (Req 9.4). */
  cancel: (orderId: Uuid) =>
    http.post<Order>(`/orders/${encodeURIComponent(orderId)}/cancel`),

  /** DELETE /orders/{id} — delete an order (Owner only, Req 9.6). */
  remove: (orderId: Uuid) =>
    http.delete<void>(`/orders/${encodeURIComponent(orderId)}`, { responseType: "void" }),
};

// =============================================================================
// Inventory  (/api/v1/inventory) — cost_per_unit omitted for Staff (Req 10.7)
// =============================================================================

export interface InventoryItemCreateBody {
  name: string;
  category: string;
  quantity: Money;
  unit: string;
  cost_per_unit: Money;
}

export interface InventoryItemUpdateBody {
  quantity?: Money;
  cost_per_unit?: Money;
}

export interface InventoryItem {
  item_id: Uuid;
  tenant_id: Uuid;
  name: string;
  category: string;
  quantity: Money;
  unit: string;
  /** Omitted entirely from the payload for Staff principals (Req 10.7). */
  cost_per_unit?: Money;
  created_at: IsoDate | null;
  updated_at: IsoDate | null;
}

export interface InventoryCategoryGroup {
  category: string;
  items: InventoryItem[];
}

export interface InventoryListResponse {
  categories: InventoryCategoryGroup[];
}

export const inventory = {
  /** POST /inventory — create an item (Req 10.1, 10.2). */
  create: (body: InventoryItemCreateBody) =>
    http.post<InventoryItem>("/inventory", { json: body }),

  /** GET /inventory — items grouped by category, alphabetically (Req 10.5). */
  list: () => http.get<InventoryListResponse>("/inventory"),

  /** PATCH /inventory/{id} — update quantity and/or cost (Req 10.3, 10.4). */
  update: (itemId: Uuid, body: InventoryItemUpdateBody) =>
    http.patch<InventoryItem>(`/inventory/${encodeURIComponent(itemId)}`, { json: body }),
};

// =============================================================================
// Recipes  (/api/v1/recipes) — cost read is Owner-only (Req 11.7)
// =============================================================================

export type RecipeComponentType = "ingredient" | "packaging";

export interface RecipeCreateBody {
  name: string;
  yield_per_batch: number;
}

export interface RecipeComponentCreateBody {
  item_name: string;
  quantity: Money;
  component_type: RecipeComponentType;
}

export interface Recipe {
  recipe_id: Uuid;
  tenant_id: Uuid;
  name: string;
  yield_per_batch: number;
  created_at: IsoDate | null;
  updated_at: IsoDate | null;
}

export interface RecipeComponent {
  component_id: Uuid;
  recipe_id: Uuid;
  item_id: Uuid;
  quantity: Money;
  type: RecipeComponentType;
}

export interface RecipeCost {
  recipe_name: string;
  yield_per_batch: number;
  ingredient_cost: Money;
  packaging_cost: Money;
  unit_cost: Money;
}

export const recipes = {
  /** POST /recipes — create a recipe (Req 11.1). */
  create: (body: RecipeCreateBody) => http.post<Recipe>("/recipes", { json: body }),

  /** POST /recipes/{id}/components — attach an ingredient/packaging (Req 11.2). */
  addComponent: (recipeId: Uuid, body: RecipeComponentCreateBody) =>
    http.post<RecipeComponent>(
      `/recipes/${encodeURIComponent(recipeId)}/components`,
      { json: body }
    ),

  /** GET /recipes — list recipes (Owner + Staff). */
  list: () => http.get<Recipe[]>("/recipes"),

  /** GET /recipes/{id}/cost — cost-per-unit breakdown (Owner only, Req 11.7). */
  getCost: (recipeId: Uuid) =>
    http.get<RecipeCost>(`/recipes/${encodeURIComponent(recipeId)}/cost`),
};

// =============================================================================
// Customers  (/api/v1/customers)
// =============================================================================

export interface CustomerCreateBody {
  name: string;
  phone: string;
  address?: string | null;
}

export interface Customer {
  customer_id: Uuid;
  tenant_id: Uuid;
  name: string;
  phone: string;
  address: string | null;
  created_at: IsoDate | null;
  updated_at: IsoDate | null;
}

export const customers = {
  /** POST /customers — create a customer (Req 12.1). */
  create: (body: CustomerCreateBody) => http.post<Customer>("/customers", { json: body }),

  /** GET /customers?q= — search by name (partial) or phone (exact), capped 50 (Req 12.2). */
  search: (q: string) => http.get<Customer[]>("/customers", { query: { q } }),
};

// =============================================================================
// Invoices  (/api/v1/invoices)
// =============================================================================

export interface InvoiceItem {
  description: string;
  quantity: number;
  unit_price: Money;
  total: Money;
}

export interface InvoiceData {
  invoice_number: string;
  issue_date: IsoDate;
  delivery_date: IsoDate;
  items: InvoiceItem[];
  subtotal: Money;
  tax_rate: Money;
  tax_label: string;
  tax_amount: Money;
  amount_paid: Money;
  amount_due: Money;
  currency: string | null;
}

export interface InvoiceGenerateBody {
  order_id: string;
  tax_rate?: Money;
  tax_label?: string | null;
}

export const invoices = {
  /** POST /invoices — generate a PDF invoice for an existing order (Req 13). */
  generate: (body: InvoiceGenerateBody) =>
    http.post<InvoicePdfResponse>("/invoices", { json: body }),
};

// =============================================================================
// Expenses  (/api/v1/expenses) — Owner only (Req 14.6)
// =============================================================================

export interface ExpenseCreateBody {
  amount: Money;
  expense_date: IsoDate;
  category: string;
  vendor_name?: string | null;
  is_capital?: boolean;
  description?: string | null;
  notes?: string | null;
}

export interface Expense {
  expense_id: Uuid;
  tenant_id: Uuid;
  amount: Money;
  expense_date: IsoDate;
  category: string;
  vendor_name: string | null;
  is_capital: string | null;
  description: string | null;
  notes: string | null;
  created_at: IsoDate | null;
}

export interface ExpenseListQuery {
  category?: string;
  start_date?: IsoDate;
  end_date?: IsoDate;
  /** Index signature so this satisfies the client's query-params type. */
  [key: string]: string | number | boolean | null | undefined;
}

export const expenses = {
  /** POST /expenses — record an expense (Owner only, Req 14.1–14.3). */
  create: (body: ExpenseCreateBody) => http.post<Expense>("/expenses", { json: body }),

  /** GET /expenses — list, optionally filtered by category + date range (Req 14.4). */
  list: (query?: ExpenseListQuery) => http.get<Expense[]>("/expenses", { query }),
};

// =============================================================================
// Insights / Ask  (/api/v1/insights) — read-only (Req 16)
// =============================================================================

export interface InsightsAskBody {
  question: string;
  start_date?: IsoDate;
  end_date?: IsoDate;
  order_id?: Uuid;
}

export interface InsightsAnswer {
  answer: string;
  answerable: boolean;
  revenue?: Money | null;
  cost?: Money | null;
  profit?: Money | null;
  order_cost?: Money | null;
  period_start?: IsoDate | null;
  period_end?: IsoDate | null;
  missing_unit_price_ingredients: string[];
}

/** A single conversation turn exchanged with the Ask/Insights chat agent. */
export interface ChatMessage {
  role: string;
  content: string;
}

export interface InsightsChatBody {
  /** The User's latest plain-language message. */
  message: string;
  /** Prior conversation turns for context; optional. */
  history?: ChatMessage[];
}

export interface InsightsChatResponse {
  /** The agent's plain-language answer. */
  answer: string;
}

export const insights = {
  /** POST /insights/ask — answer a plain-language business question (Req 16). */
  ask: (body: InsightsAskBody) => http.post<InsightsAnswer>("/insights/ask", { json: body }),

  /**
   * POST /insights/chat — chat with the LLM agent (full tools + DB access).
   * Send the latest `message` plus prior `history`; get back `{ answer }`.
   */
  chat: (body: InsightsChatBody) =>
    http.post<InsightsChatResponse>("/insights/chat", { json: body }),
};

// =============================================================================
// Image ingestion  (/api/v1/ingestion) — confirm-and-edit flow (Req 15)
// =============================================================================

export type IngestionDocType =
  | "receipt"
  | "recipe"
  | "order"
  | "catalog"
  | "payment"
  | "inventory";

export interface IngestionDraftResponse {
  doc_type: IngestionDocType;
  confidence: number;
  raw_text: string | null;
  /** Doc-type-specific structured fields for the editable UI form. */
  draft: Record<string, unknown>;
}

export interface IngestionConfirmBody {
  doc_type: IngestionDocType;
  draft: Record<string, unknown>;
}

export const ingestion = {
  /**
   * POST /ingestion/extract — multipart image + doc_type → typed draft (Req 15.1).
   * Rejects >10 MB / unsupported formats (400) and extraction failure (422).
   */
  extract: (docType: IngestionDocType, image: File | Blob) => {
    const form = new FormData();
    form.append("doc_type", docType);
    form.append("image", image);
    return http.post<IngestionDraftResponse>("/ingestion/extract", { formData: form });
  },

  /**
   * POST /ingestion/confirm — persist a (possibly edited) draft (Req 15.4, 15.6).
   * The response shape depends on the doc type's domain create; typed loosely.
   */
  confirm: (body: IngestionConfirmBody) =>
    http.post<Record<string, unknown>>("/ingestion/confirm", { json: body }),
};

/** Re-export so callers can `import { ApiError, http } from "./endpoints"` if preferred. */
export { ApiError, http, setDeviceToken, getDeviceToken } from "./client";
export type { ApiRequestOptions };
