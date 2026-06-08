# Register Mode — Implementation Notes

> **This document reflects the as-built implementation.** For the original design exploration, see git history.

## Overview

Register Mode is a no-LLM, mobile-first web interface for selling at events (fairs, exhibitions, pop-ups) or during regular sales days. The owner opens the URL on their phone, selects products, and starts selling. No LLM calls happen in the web UI — pure CRUD.

**URL:** `/register/{tenant_id}` (bookmarkable, static per owner)
**Alias:** `/booth/{tenant_id}` (backward compat)

---

## Current Features (Phase 1 — built)

| Feature | Status |
|---|---|
| Session setup via web UI (multi-select products, set prices) | ✅ |
| Session setup via Telegram (auto-creates with all products) | ✅ |
| Regular Day mode (open-ended, owner ends manually) | ✅ |
| Event/Booth mode (N-day duration, auto-ends at expiry) | ✅ |
| Event countdown in header | ✅ |
| Product grid: tap +/−, cart, checkout | ✅ |
| Per-item note + extra charge (packaging, customisation) | ✅ |
| GST field at checkout (applied to subtotal) | ✅ |
| Cash / UPI (manual confirm) | ✅ |
| Razorpay dynamic QR | ✅ (needs live keys to test) |
| Thermal receipt (browser print) | ✅ |
| PDF invoice with GST (download) | ✅ |
| Orders view (📋 button in header) | ✅ |
| Cancel sale | ✅ |
| End register session | ✅ |
| Add custom products (not in catalog) | ✅ |
| Session persists across devices / tab reloads | ✅ |
| Booth session reporting via Telegram chat | ✅ |

## Phase 2 (planned)

| Feature | Notes |
|---|---|
| Razorpay webhook end-to-end test | Webhook handler built; needs live Razorpay keys |
| Per-day breakdown for multi-day events | Split orders view by date |
| Low stock alert (Telegram notification) | When remaining = 0 |
| Register Mode PIN | Short 4-digit PIN per tenant for access control |
| Inventory deduction on booth sale | Auto-reduce ingredient stock |
| PWA / offline support | Cache product list for connectivity issues |

---

## Telegram Integration

Owner says **"setup my register"** or **"setup booth"** → agent immediately calls `create_booth_from_categories(name="Register", categories=["all"])` → sends the URL. No questions asked. Owner names the session and adjusts products on the web page.

If owner mentions an event name (e.g. "set up for Delhi Food Fest"), that name is used instead.

---

## Mode Behaviour

**Regular:** Session stays open until owner taps "End Register". No expiry.

**Event:** Owner sets number of days on the setup screen. `ends_at = started_at + duration_days`. On every page load, `BoothService.auto_end_if_expired()` checks and ends the session automatically. Countdown shown in header.

---

## Data Flow

```
Web UI → POST /register/{id}/api/session/create
       → POST /register/{id}/api/checkout
            → BoothService.checkout()
                → Creates Order (booth_session_id set, status=delivered)
                → Creates OrderItems (recipe_name = "Product — size_label")
                → Creates Payment
                → Increments sold_qty on BoothSessionItem
```

Orders created by Register Mode are linked to the booth session via `booth_session_id`. The agent can query these with `get_booth_session_summary`.

---

## File Map

```
app/booth/
├── router.py               ← /register/ routes + /booth/ alias
├── booth_service.py        ← Session + checkout + reporting
├── razorpay_client.py      ← Razorpay QR creation
├── razorpay_router.py      ← Razorpay webhook
├── static/
│   ├── css/booth.css
│   └── js/
│       ├── api.js          ← All fetch calls
│       ├── app.js          ← Boot, screen switching, countdown, orders view
│       ├── cart.js         ← Cart state, product grid rendering
│       ├── checkout.js     ← Checkout sheet, GST, per-item extras
│       ├── receipt.js      ← Receipt render, invoice download
│       ├── setup.js        ← Setup screen, mode toggle, createBooth()
│       └── qr.js           ← Razorpay QR polling
└── templates/
    ├── booth.html          ← SPA (all screens)
    └── receipt.html        ← Standalone printable receipt
```


## Overview

Exhibition Booth Mode is a no-LLM, mobile-first web interface that lets an owner
sell pre-baked products at events (fairs, exhibitions, pop-ups). The owner sets
up the booth session entirely via Telegram chat — specifying which products,
prices, and quantities. The web UI is purely a sell screen: tap products, adjust
quantities, checkout. Sales are recorded in the existing DB and linked to a named
booth session so they can be queried later ("how much did we sell at the Pune
Food Fest?").

**Key constraints:**
- Zero LLM calls in the web UI — pure CRUD, sub-100ms responses
- Session setup happens in Telegram chat (LLM parses product list once)
- Session state persisted in DB — opening the booth URL on any device (phone,
  tablet, another tab) loads the active session automatically, no re-setup needed
- No new infrastructure — served by the existing FastAPI process on port 8000
- Reuses existing models: `Order`, `OrderItem`, `Payment`, `Product`, `ProductVariant`
- Static URL per owner — bookmarkable, shareable
- Works on phone/tablet browser, offline-tolerant cart (JS in-memory)
- Receipt prints via browser `@media print` to any paired thermal printer

---

## Phase 1 Scope

| In scope | Out of scope (Phase 2+) |
|---|---|
| Session setup via Telegram chat | Barcode / QR scanning |
| Sell screen: product grid, cart, checkout | Offline sync / PWA |
| Per-item booth price (overrides catalog price) | Barcode label printing |
| Per-item stock quantity (nullable = unlimited) | Stripe integration |
| Sold-out tracking | Inventory deduction |
| Cash / UPI (manual confirm) checkout | Multi-device session sync |
| Razorpay dynamic QR checkout | |
| Browser-print thermal receipt | |
| Session persists across devices / tab reloads | |
| Booth session reporting via chat | |
| Booth session tracking | Customer loyalty / history |
| "Open booth" command in Telegram | |
| End-of-session summary | |

---

## URL Structure

```
GET  /booth/{tenant_id}                    ← single-page app (bookmarkable)
GET  /booth/{tenant_id}/api/products       ← product list for setup screen
POST /booth/{tenant_id}/api/session/start  ← create BoothSession
POST /booth/{tenant_id}/api/session/end    ← close BoothSession
GET  /booth/{tenant_id}/api/session/active ← get active session (if any)
POST /booth/{tenant_id}/api/checkout       ← create order + payment
GET  /booth/{tenant_id}/api/receipt/{order_id} ← receipt HTML (for print)
POST /razorpay/webhook                     ← Razorpay payment.captured events
```

`tenant_id` is the existing UUID — no new auth needed. The URL is unguessable
(UUID) and the owner bookmarks it. For additional security a PIN can be added
in Phase 2.

---

## Data Model Changes

### 1. `BoothSession` (new table)

```python
class BoothSession(Base):
    __tablename__ = "booth_sessions"

    session_id   = Column(PortableUUID, primary_key=True, default=uuid4)
    tenant_id    = Column(PortableUUID, ForeignKey("tenants.tenant_id"), nullable=False, index=True)
    name         = Column(String, nullable=False)          # e.g. "Pune Food Fest May 2026"
    started_at   = Column(DateTime, nullable=False, default=datetime.utcnow)
    ended_at     = Column(DateTime, nullable=True)         # NULL = session still active
    created_at   = Column(DateTime, nullable=False, default=datetime.utcnow)

    # Relationships
    items  = relationship("BoothSessionItem", back_populates="session", cascade="all, delete-orphan")
    orders = relationship("Order", back_populates="booth_session")
```

### 2. `BoothSessionItem` (new table)

Stores which product variants are available in a session, at what price, and
how many units the owner brought. This is what persists across devices — opening
the booth URL on any device loads these items directly.

```python
class BoothSessionItem(Base):
    __tablename__ = "booth_session_items"

    item_id      = Column(PortableUUID, primary_key=True, default=uuid4)
    session_id   = Column(PortableUUID, ForeignKey("booth_sessions.session_id"), nullable=False, index=True)
    variant_id   = Column(PortableUUID, ForeignKey("product_variants.variant_id"), nullable=False)
    booth_price  = Column(Numeric(10, 2), nullable=False)  # may differ from catalog price
    stock_qty    = Column(Integer, nullable=True)           # NULL = unlimited
    sold_qty     = Column(Integer, nullable=False, default=0)  # incremented on checkout

    # Relationships
    session = relationship("BoothSession", back_populates="items")
    variant = relationship("ProductVariant")
```

`sold_qty >= stock_qty` (when stock_qty is not NULL) → item shows as "Sold Out"
on the sell screen.

### 3. `Order` — add `booth_session_id` (new nullable FK)

```python
booth_session_id = Column(PortableUUID, ForeignKey("booth_sessions.session_id"), nullable=True, index=True)
```

NULL for all regular chat orders. Set for booth orders. This is the link that
lets the agent answer "how much did we sell at that event".

### 4. `Tenant` — add Razorpay credentials

```python
razorpay_key_id     = Column(String, nullable=True)   # public key — safe to store as-is
razorpay_key_secret = Column(String, nullable=True)   # secret key — encrypt at rest (Phase 2)
```

Note: Encryption at rest for `razorpay_key_secret` is deferred to Phase 2.
For Phase 1 it is stored as plaintext in the tenant's SQLite file (same
security posture as the existing `instagram_access_token`).

### 5. `Payment` — add `razorpay_payment_id` (new nullable column)

```python
razorpay_payment_id = Column(String, nullable=True)   # Razorpay payment ID for reconciliation
```

### Migration

One Alembic migration file covering all five changes above. For SQLite tenants,
`Base.metadata.create_all()` handles new tables automatically on next startup;
`ALTER TABLE` statements handle new columns on existing tables.

---

## Module Structure

New code lives in two places, following existing conventions:

```
app/
├── booth/                          ← new package
│   ├── __init__.py
│   ├── router.py                   ← FastAPI router (all /booth/* endpoints)
│   ├── razorpay_router.py          ← FastAPI router (/razorpay/webhook)
│   ├── booth_service.py            ← business logic (no LLM, pure CRUD)
│   ├── razorpay_client.py          ← Razorpay API wrapper (httpx)
│   └── templates/
│       ├── booth.html              ← single-page app (setup + sell + receipt screens)
│       └── receipt.html            ← standalone printable receipt (for /receipt/{id})
├── models.py                       ← add BoothSession, update Order + Tenant + Payment
├── services/
│   └── reporting_service.py        ← extend with booth session reporting
└── tools/
    └── other_tools.py              ← add get_booth_url, booth session query tools
```

### Why a separate `app/booth/` package?

- Keeps booth code isolated — easy to find, easy to delete if needed
- Follows the existing pattern: `app/handlers/`, `app/services/`, `app/tools/`
- `router.py` and `razorpay_router.py` are registered on the existing FastAPI
  `app` in `webhook_server.py` — no new process, no new port

---

## Component Responsibilities

### `app/booth/booth_service.py`

Pure business logic. No HTTP, no LLM. All methods take a `db: Session` and
`tenant_id: UUID`. Returns dataclasses or raises `ValueError`.

```python
class BoothService:
    def __init__(self, db: Session, tenant_id: UUID)

    # Session management
    def start_session(name: str) -> BoothSession
    def end_session(session_id: UUID) -> BoothSession
    def get_active_session() -> Optional[BoothSession]
    def get_session(session_id: UUID) -> BoothSession

    # Product listing (for setup screen)
    def list_products() -> List[ProductWithVariants]
        # Returns all products with variants, sorted by category then name

    # Checkout
    def checkout(
        session_id: UUID,
        items: List[BoothItemInput],   # [{variant_id, quantity}]
        payment_method: str,           # "cash" | "upi" | "razorpay"
        customer_name: Optional[str],
    ) -> BoothOrder
        # Creates Order (status="delivered", booth_session_id set)
        # Creates OrderItems from variant prices
        # Creates Payment record (pending for razorpay, completed for cash/upi)
        # Returns order_id + total for receipt / Razorpay QR

    # Reporting (used by agent for "how much did we sell at X")
    def get_session_summary(session_id: UUID) -> SessionSummary
    def list_sessions() -> List[BoothSession]
```

**Reuse from existing services:**
- `ProductService.list_products()` — already exists, reuse directly
- `OrderService` — NOT reused directly. Booth checkout has different
  validation rules (no delivery date, no customer resolution by name/phone,
  status is immediately "delivered"). `BoothService.checkout()` writes to
  the same `orders` / `order_items` / `payments` tables directly.
- `AuditService` — reuse for logging booth checkouts

### `app/booth/razorpay_client.py`

Thin wrapper around Razorpay REST API using `httpx` (already in requirements).

```python
class RazorpayClient:
    def __init__(self, key_id: str, key_secret: str)

    async def create_qr_code(amount_paise: int, order_ref: str) -> RazorpayQR
        # POST /v1/payments/qr_codes
        # Returns: qr_id, image_url, amount

    async def verify_webhook_signature(body: bytes, signature: str) -> bool
        # HMAC-SHA256 verification using key_secret

    async def close()
```

Razorpay amounts are in **paise** (1 INR = 100 paise). Conversion happens in
`BoothService`, not in the client.

### `app/booth/router.py`

FastAPI router. Thin HTTP layer — validates input, calls `BoothService`,
returns JSON. No business logic here.

```python
router = APIRouter(prefix="/booth/{tenant_id}", tags=["booth"])

GET  /                          → serve booth.html (Jinja2 template)
GET  /api/products              → list products + variants
GET  /api/session/active        → get active session or null
POST /api/session/start         → {name: str} → BoothSession
POST /api/session/end           → {session_id: str} → BoothSession
POST /api/checkout              → CheckoutRequest → CheckoutResponse
GET  /api/receipt/{order_id}    → serve receipt.html
```

**Dependency injection** — follows FastAPI patterns:

```python
def get_booth_db(tenant_id: str) -> Generator[Session, None, None]:
    """Open the correct tenant DB for booth requests."""
    # Validates tenant_id exists, opens per-tenant DB session
    # Raises 404 if tenant not found
```

### `app/booth/razorpay_router.py`

Separate router for Razorpay webhooks (no tenant_id in path — Razorpay sends
to a single endpoint).

```python
router = APIRouter(prefix="/razorpay", tags=["razorpay"])

POST /webhook
    # 1. Verify HMAC-SHA256 signature using tenant's key_secret
    # 2. Parse event type (payment.captured)
    # 3. Find order by razorpay_payment_id
    # 4. Update payment status to "completed"
    # 5. Return 200 OK immediately (Razorpay retries on non-200)
```

**Challenge:** Razorpay sends one webhook endpoint but we have multiple tenants
each with their own key_secret. Solution: store `razorpay_payment_id` on the
payment record at QR creation time. On webhook receipt, look up the payment by
`razorpay_payment_id`, find the tenant, verify signature with that tenant's
key_secret.

### `app/booth/templates/booth.html`

Single HTML file, vanilla JS, no build step. Three screens managed by JS
`showScreen('setup' | 'sell' | 'receipt')`.

**Screen 1 — Setup**
```
[ Event name input field ]
[ Product checklist — fetched from /api/products ]
  ☑ Chocolate Truffle Cake
    ☑ 500g — ₹800
    ☑ 1kg  — ₹1,500
  ☑ Oatmeal Raisin Cookies
    ☑ 250g — ₹400
  ☐ Vanilla Muffin
[ Start Exhibition button ]
```

Products grouped by category. Variants shown indented under each product.
Owner selects which specific variants to show on the sell screen.

**Screen 1 — Sell (loads directly when active session exists)**
```
[ "Pune Food Fest" · 12 sales · ₹4,800  |  [End Session] ]

[ Product grid — 2 columns on phone ]
  ┌─────────────────┐  ┌─────────────────┐
  │ Choc Truffle    │  │ OAT Cookies     │
  │ 500g — ₹800     │  │ 250g — ₹400     │
  │  [-]  0  [+]   │  │  [-]  0  [+]   │
  │ 18 left         │  │ 30 left         │
  └─────────────────┘  └─────────────────┘
  ┌─────────────────┐
  │ Vanilla Muffin  │
  │ ₹50             │
  │  [-]  0  [+]   │
  │ ∞ (unlimited)   │
  └─────────────────┘

  SOLD OUT items shown greyed out, [+] disabled

[ Cart summary — sticky bottom bar ]
  3 items · ₹2,400        [ Checkout → ]
```

If no active session exists, show a message: "No active booth session.
Set one up in Telegram by saying 'set up booth'."

**Screen 3 — Checkout**
```
[ Order summary ]
  Choc Truffle 500g × 2 = ₹1,600
  OAT Cookies 250g × 1 = ₹400
  ─────────────────────────────
  Total: ₹2,000

[ Customer name (optional) ]

[ Payment method ]
  [ Cash ]  [ UPI ]  [ Razorpay ]

  → Cash:     [ Confirm received ]
  → UPI:      [ QR image ]  [ Confirm received ]
  → Razorpay: [ Dynamic QR — auto-confirms on payment ]

[ Cancel ]
```

**Screen 3b — Receipt (after checkout)**
```
[ Receipt — formatted for 80mm thermal paper ]
  BUSINESS NAME
  ─────────────
  Date: 13 May 2026
  Order: #1234

  Choc Truffle 500g  × 2  ₹1,600
  OAT Cookies 250g   × 1    ₹400
  ─────────────────────────────
  Total              ₹2,000
  Paid (Cash)        ₹2,000

  Thank you!

[ Print Receipt ]  [ New Sale ]
```

### `app/booth/templates/receipt.html`

Standalone receipt page served at `/booth/{tenant_id}/api/receipt/{order_id}`.
Used when the owner wants to reprint a receipt. Same 80mm CSS as the inline
receipt screen.

```css
@media print {
  @page {
    size: 80mm auto;
    margin: 4mm 4mm;
  }
  .no-print { display: none; }
  body { font-family: monospace; font-size: 11px; }
}
```

---

## Razorpay Integration Flow

### One-time setup (owner does this once via Telegram chat)

```
Owner: "connect razorpay"
Bot:   "Please provide your Razorpay Key ID and Key Secret.
        You can find these at razorpay.com → Settings → API Keys."
Owner: "rzp_live_abc123  /  secretxyz"
Bot:   "✅ Razorpay connected. Dynamic QR payments are now enabled in booth mode."
```

Keys stored in `tenants` table (`razorpay_key_id`, `razorpay_key_secret`).

### Checkout flow with Razorpay

```
1. Owner taps "Razorpay" at checkout
2. POST /booth/{tenant_id}/api/checkout
   → BoothService.checkout() creates Order + Payment (status="pending")
   → RazorpayClient.create_qr_code(amount_paise, order_ref) called
   → QR image_url + razorpay_qr_id stored on Payment
   → Response: {order_id, qr_image_url, amount}
3. Booth UI shows QR image fullscreen
4. Customer scans QR with any UPI app, pays
5. Razorpay sends POST /razorpay/webhook (payment.captured)
6. Webhook handler:
   → Verifies HMAC signature
   → Finds Payment by razorpay_payment_id
   → Updates Payment.status = "completed"
   → Returns 200 OK
7. Booth UI polls GET /booth/{tenant_id}/api/checkout/status/{order_id}
   every 2 seconds until status = "completed"
8. UI transitions to receipt screen automatically
```

### Polling endpoint

```
GET /booth/{tenant_id}/api/checkout/status/{order_id}
→ {"status": "pending" | "completed" | "failed"}
```

Simple DB read. No LLM, no Razorpay API call. Polling interval: 2 seconds,
timeout: 5 minutes (then show "Payment not received — try again").

---

## Telegram Integration

### Session setup — entirely in chat

The owner never needs to touch the web UI to set up a session. The LLM handles
the entire setup conversation and creates the session + items in the DB. The
booth URL opens directly to the sell screen.

```
Owner: "I want to set up a booth for Pune Food Fest"

Bot:   "Let's set it up! Which products are you selling and at what price?
        Here's your catalog:
        • Chocolate Truffle Cake — 500g ₹800, 1kg ₹1,500
        • OAT Cookies — 250g ₹400
        • Vanilla Muffin — per piece ₹50

        Tell me which ones, their booth price, and how many you're bringing
        (quantity is optional — leave it out for unlimited).

        Example: 'Choc Truffle 500g ₹800 qty 20, OAT Cookies ₹400 qty 30'"

Owner: "Choc Truffle 500g ₹800 qty 20, OAT Cookies 250g ₹400 qty 30,
        Vanilla Muffin ₹50"

Bot:   "✅ Booth 'Pune Food Fest' is ready!

        Products:
        • Chocolate Truffle Cake 500g — ₹800 (20 units)
        • OAT Cookies 250g — ₹400 (30 units)
        • Vanilla Muffin — ₹50 (unlimited)

        Open your booth on any device:
        https://yourdomain.com/booth/864f5be6-...

        Bookmark it — the link never changes."
```

The LLM calls `create_booth_session` tool with the parsed items. The tool
creates `BoothSession` + `BoothSessionItem` records in one transaction.

### "Open booth" command (existing session)

```
Owner: "open booth" / "booth link" / "exhibition mode"
Bot:   → calls get_booth_url tool
       → if active session exists: "Your booth is live: <url>"
       → if no active session: "No active booth. Say 'set up booth' to create one."
```

### Updating a session mid-event

```
Owner: "add Brownies 500g ₹600 qty 15 to the booth"
Bot:   → calls add_booth_item tool
       → "✅ Added Brownies 500g ₹600 (15 units) to your booth.
          It's live on the sell screen now."
```

Changes reflect immediately on any open booth tab (next page load / refresh).

### End of session

```
Owner: "end booth session" / "close the booth"
Bot:   → calls end_booth_session tool
       → "✅ Pune Food Fest session closed.
          Summary: 47 sales · ₹18,400 revenue · 3h 20m"
```

### Booth session reporting via chat

```
Owner: "How much did we sell at the Pune Food Fest?"
Bot:   → calls get_booth_session_summary tool
       → "Pune Food Fest (12 May 2026, 3h 20m):
          47 sales · ₹18,400 revenue
          Top sellers:
          • Chocolate Truffle Cake 500g — 18 units · ₹14,400
          • OAT Cookies 250g — 12 units · ₹4,800
          • Vanilla Muffin — 17 units · ₹850"
```

### New tools added to `other_tools.py`

| Tool | Description |
|---|---|
| `create_booth_session` | Creates session + items from owner's product list |
| `add_booth_item` | Adds a product variant to an active session |
| `remove_booth_item` | Removes a product variant from an active session |
| `end_booth_session` | Closes the active session, returns summary |
| `get_booth_url` | Returns the booth URL for this tenant |
| `list_booth_sessions` | Lists past sessions with dates and totals |
| `get_booth_session_summary` | Detailed breakdown for a specific session |

---

## Registration in `webhook_server.py`

```python
# In webhook_server.py — add after existing router registrations

def register_booth():
    from app.booth.router import router as booth_router
    from app.booth.razorpay_router import router as razorpay_router
    app.include_router(booth_router)
    app.include_router(razorpay_router)
    # Serve static templates via Jinja2
    from fastapi.templating import Jinja2Templates
    # templates object passed to booth router
```

Called once at startup from `telegram_listener.py`, same pattern as
`register_instagram()`.

---

## Dataclasses / Request-Response Schemas

```python
# app/booth/booth_service.py

@dataclass
class BoothItemInput:
    variant_id: UUID
    quantity: int

@dataclass
class BoothOrder:
    order_id: UUID
    total_amount: Decimal
    items: List[BoothOrderItem]
    payment_method: str
    razorpay_qr_url: Optional[str]   # set only for Razorpay payments

@dataclass
class BoothOrderItem:
    product_name: str
    variant_label: str
    quantity: int
    unit_price: Decimal
    line_total: Decimal

@dataclass
class SessionSummary:
    session_id: UUID
    name: str
    started_at: datetime
    ended_at: Optional[datetime]
    total_orders: int
    total_revenue: Decimal
    items_sold: int
    top_products: List[Dict]   # [{name, quantity, revenue}]
```

Pydantic models for FastAPI request/response validation (separate from
service dataclasses):

```python
# app/booth/router.py

class StartSessionRequest(BaseModel):
    name: str

class CheckoutRequest(BaseModel):
    session_id: str
    items: List[CheckoutItem]
    payment_method: Literal["cash", "upi", "razorpay"]
    customer_name: Optional[str] = None

class CheckoutItem(BaseModel):
    variant_id: str
    quantity: int = Field(ge=1)
```

---

## Config Changes

Add to `app/config.py`:

```python
# Booth
BOOTH_RAZORPAY_WEBHOOK_SECRET: str = ""
# Per-tenant Razorpay keys are stored in the tenants table, not here.
# This field is reserved for a future platform-level Razorpay account.
```

---

## Migration File

```
migrations/versions/h9i0j1k2l3m4_add_booth_tables.py
```

Changes:
1. Create `booth_sessions` table
2. Create `booth_session_items` table
3. Add `booth_session_id` (nullable FK) to `orders`
4. Add `razorpay_key_id`, `razorpay_key_secret` to `tenants`
5. Add `razorpay_payment_id` to `payments`

For SQLite: `create_all` handles new tables; `ALTER TABLE ADD COLUMN` for
new columns on existing tables.

---

## Build Order

Build in this sequence to avoid forward dependencies:

1. **Models** — add `BoothSession`, `BoothSessionItem`, update `Order`, `Tenant`, `Payment`
2. **Migration** — create and apply to existing DBs
3. **`BoothService`** — core business logic (session CRUD, checkout, reporting)
4. **`RazorpayClient`** — Razorpay API wrapper
5. **`router.py`** — FastAPI endpoints
6. **`razorpay_router.py`** — webhook handler
7. **`booth.html`** — sell screen + checkout + receipt (no setup screen)
8. **`receipt.html`** — standalone reprint template
9. **Register routers** in `webhook_server.py`
10. **Telegram tools** — `create_booth_session`, `add_booth_item`, `remove_booth_item`,
    `end_booth_session`, `get_booth_url`, `list_booth_sessions`, `get_booth_session_summary`
11. **System prompt** — add booth setup rules
12. **Tests** — `tests/test_booth_service.py`

---

## Testing Plan

`tests/test_booth_service.py` covers:

- `test_start_session` — creates session, returns session_id
- `test_end_session` — sets ended_at
- `test_get_active_session` — returns active, None if none
- `test_checkout_cash` — creates order + payment, status=completed
- `test_checkout_razorpay` — creates order + payment, status=pending
- `test_checkout_sets_booth_session_id` — order.booth_session_id is set
- `test_checkout_uses_variant_price` — selling_price taken from ProductVariant
- `test_session_summary` — correct totals and top products
- `test_list_sessions` — returns all sessions for tenant

`tests/test_razorpay_client.py` covers:
- `test_verify_webhook_signature_valid`
- `test_verify_webhook_signature_invalid`
- `test_create_qr_code` (mocked httpx)

---

## What Is Explicitly NOT Changed

- `OrderService` — booth checkout does NOT go through `OrderService`. It writes
  directly to the same tables but with different validation rules. This avoids
  polluting `OrderService` with booth-specific logic.
- `AgentService` / `LLMClient` — not touched. Booth has zero LLM calls.
- `RequestHandler` — not touched. Booth is a separate HTTP path.
- `TelegramBotListener` — not touched. Booth URL is returned as a plain string
  by the `get_booth_url` tool, same as any other tool response.
- Existing migrations — not modified. New migration appended.
- Existing tests — not modified. New test file added.

---

## Open Questions (resolve before building)

1. **UPI QR image** — does the owner upload a static UPI QR image, or do we
   generate one from their UPI ID? Simplest: owner pastes their UPI ID, we
   display it as text + generate a QR via a JS library (no server call needed).

2. **Customer name at booth** — is it required, optional, or skipped entirely?
   Recommendation: optional. If provided, we create/find a customer record.
   If not, order is created with a placeholder "Walk-in Customer".

3. **Multiple active sessions** — can an owner have two booth sessions running
   simultaneously? Recommendation: one active session per tenant for Phase 1.
   Enforce at service layer (not DB constraint) for simplicity.

4. **Razorpay QR timeout** — Razorpay QR codes expire after 10 minutes by
   default. What should the UI show when a QR expires? Recommendation: show
   a "QR expired — tap to regenerate" button that calls checkout again.

5. **`sold_qty` concurrency** — if two staff members check out simultaneously
   on different devices, `sold_qty` could be incremented twice without checking
   stock. Recommendation: use a DB-level check (`sold_qty + new_qty <= stock_qty`)
   inside the checkout transaction, return a clear error if stock is exceeded.
