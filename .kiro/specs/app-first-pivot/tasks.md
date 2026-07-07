# Implementation Plan: App-First Pivot

## Overview

This plan turns the App-First Pivot design into incremental coding tasks that follow the design's **phased, additive, Telegram-safe rollout**: Phase 0 (data & migration) → Phase 1 (auth + API foundation) → Phase 2 (app surfaces, tab by tab) → Phase 3 (image ingestion) → Phase 4 (insights) → Phase 5 (notifications demotion). The system stays shippable at the end of every phase because each step is an additive mount over the existing FastAPI app; the polling bot and booth router are never removed.

All domain logic reuses the **existing, unchanged service layer** (`OrderService`, `InventoryService`, `RecipeService`, `ProductService`, `CustomerService`, `PaymentService`, `InvoiceService`, `ReportingService`, `BoothService`). New code is concentrated in `app/api/` (versioned REST routers, deps, schemas, errors), `app/auth/` (Auth_Service, OTP delivery, WebAuthn, security, registry-DB auth models), `app/notifications/`, and `frontend/` (React + TypeScript + Ionic PWA).

Property-based tests use **`hypothesis` (Python)** for backend properties and **`fast-check` (TypeScript)** for frontend logic (tab visibility, cart total, offline at-most-once). Each property test is tagged with `# Feature: app-first-pivot, Property {n}` and cites the requirement clauses it validates. Runtime/PWA, WebAuthn ceremonies, image extraction, and migration are covered by unit/integration tests, not PBT, per the design's Testing Strategy.

## Tasks

- [x] 1. Phase 0 — Data models and migration (registry auth tables + per-tenant attribution/idempotency)
  - [x] 1.1 Create registry-DB auth models
    - Add `app/auth/__init__.py` and `app/auth/models_auth.py` defining `User`, `Device`, `OtpChallenge`, and `WebAuthnCredential` registered on the existing `Base`, using `PortableUUID` primary keys and the `UniqueConstraint("tenant_id","phone")` on `User`, per the Data Models section
    - Include `pin_hash`, `pin_failed_count`, `pin_locked_until` on `User`; `token_hash`/`expires_at`/`revoked_at` on `Device`; `code_hash`/`expires_at`/`attempt_count`/`consumed_at`/`delivery_channel` on `OtpChallenge`
    - _Requirements: 5.1, 2.6, 2.8, 2.9, 4.1, 4.5, 19.1_

  - [x] 1.2 Add attribution and idempotency to per-tenant business models
    - In `app/models.py`, add nullable indexed `Order.created_by_user_id` (application-layer reference, no DB FK) and a new `SellIdempotency` table (`idempotency_key` PK, `tenant_id`, `order_id`, `created_at`)
    - _Requirements: 7.1, 7.3, 8.3, 18.5_

  - [x] 1.3 Write the migration runner
    - Create `scripts/migrate_app_first.py` that runs idempotent `create_all` on the registry engine (new auth tables + `SellIdempotency` per tenant) and performs the per-tenant SQLite ALTER sweep over `get_all_tenant_db_paths()` adding `orders.created_by_user_id` guarded by a `PRAGMA table_info` existence check; delegate to `alembic upgrade head` for Postgres
    - _Requirements: 7.1, 8.3, 19.1_

  - [ ]* 1.4 Write migration unit test (not PBT)
    - `tests/test_migrate_app_first.py`: run the ALTER sweep against a fixture set of tenant SQLite files, assert the column is added, assert re-running is safe (idempotent), and assert a freshly created tenant file gets the column via `create_all`
    - _Requirements: 7.1_

- [ ] 2. Checkpoint — Phase 0 complete
  - Ensure all tests pass and the migration runner executes cleanly against a fixture registry + tenant set. Ask the user if questions arise.

- [x] 3. Phase 1 — Auth_Service and security primitives
  - [x] 3.1 Implement security primitives
    - Create `app/auth/security.py`: argon2 PIN hashing, salted short-lived OTP hashing, high-entropy device token generation with SHA-256 storage hash, and constant-time comparison helpers
    - _Requirements: 2.5, 4.1_

  - [x] 3.2 Implement AuthService OTP request/verify
    - Create `app/auth/auth_service.py` with `request_otp` (phone format validation, phone→tenant/user resolution, 4–8 digit OTP generation, hash+store with 5-min expiry) and `verify_otp` (constant-time compare, 5-attempt cap, expiry + single-use consumption, mint Device_Session_Token clamped to 30–90 days, return raw token once)
    - _Requirements: 2.1, 2.2, 2.3, 2.5, 2.6, 2.7, 2.8, 2.9_

  - [x] 3.3 Implement tiered OTP delivery
    - Create `app/auth/otp_sender.py` with the `OtpSender` protocol, `NotificationChannelSender` (reusing existing telegram/whatsapp send paths), a config-abstracted `SmsSender`, and `TieredOtpSender` (channel-first within 5s; SMS fallback on no/unconfirmed channel; delivery-failure with OTP not marked delivered if both unconfirmed within 30s)
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 2.4_

  - [x] 3.4 Implement AuthService PIN and lockout
    - Extend `app/auth/auth_service.py` with `set_pin` (validate 4–8 numeric digits, argon2 hash, store nothing on invalid) and `verify_pin` (grant on match, deny+unchanged on mismatch, lock PIN entry 300s after 5 consecutive failures)
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5_

  - [x] 3.5 Implement AuthService user management and mode elevation
    - Extend `app/auth/auth_service.py` with `create_user`/`list_users` (Owner-only, new Staff bound to the Owner's tenant, exactly one role) and `elevate_to_manage` (requires Owner-role PIN/WebAuthn, lockout 30s after 5 failures; user switching by PIN/WebAuthn without OTP)
    - _Requirements: 5.1, 5.5, 4.9, 6.3, 6.4, 6.5, 6.6, 6.7_

  - [x] 3.6 Implement WebAuthn service
    - Create `app/auth/webauthn_service.py` wrapping the `webauthn` library: registration (attestation) and authentication (assertion) ceremonies, storing/updating `WebAuthnCredential` (credential id, public key, sign count)
    - _Requirements: 4.6, 4.7, 4.8_

  - [ ]* 3.7 Write property test — OTP well-formed and single-use (hypothesis)
    - **Property 6: Generated OTP is well-formed and single-use within its validity window**
    - **Validates: Requirements 2.2, 2.5**

  - [ ]* 3.8 Write property test — OTP failure modes (hypothesis)
    - **Property 7: OTP verification fails for wrong, expired, or over-attempted codes**
    - **Validates: Requirements 2.7, 2.8, 2.9**

  - [ ]* 3.9 Write property test — invalid phone produces no challenge (hypothesis)
    - **Property 8: Invalid phone numbers never produce an OTP challenge**
    - **Validates: Requirements 2.3**

  - [ ]* 3.10 Write property test — device token validity window (hypothesis)
    - **Property 9: Issued device session token validity is within 30 to 90 days**
    - **Validates: Requirements 2.6**

  - [ ]* 3.11 Write property test — tiered OTP delivery (hypothesis, senders mocked)
    - **Property 10: Tiered OTP delivery selects channel-first then SMS fallback**
    - **Validates: Requirements 3.1, 3.2, 3.3, 3.4**

  - [ ]* 3.12 Write property test — PIN validate/hash/verify (hypothesis)
    - **Property 11: PIN is a validated, hashed, verifiable secret**
    - **Validates: Requirements 4.1, 4.2, 4.3, 4.4**

  - [ ]* 3.13 Write property test — consecutive-failure lockout (hypothesis)
    - **Property 12: Consecutive-failure lockout holds for its configured window**
    - **Validates: Requirements 4.5, 6.7**

  - [ ]* 3.14 Write property test — elevation and user switching (hypothesis)
    - **Property 13: Manage_Mode elevation and user switching require a valid owner credential and no OTP**
    - **Validates: Requirements 4.9, 6.3, 6.6**

  - [ ]* 3.15 Write property test — role/mode assignment determinism (hypothesis)
    - **Property 14: Sign-in mode and created-user role/tenant are assigned deterministically**
    - **Validates: Requirements 5.1, 5.5, 6.4, 6.5**

  - [ ]* 3.16 Write WebAuthn integration tests (not PBT)
    - Integration tests in `tests/test_webauthn_service.py` against the `webauthn` library using canned attestation/assertion fixtures for register + verify success and failure
    - _Requirements: 4.6, 4.7, 4.8_

- [ ] 4. Phase 1 — API foundation (deps, schemas, errors, auth router, mounting)
  - [x] 4.1 Implement shared HTTP error mapping
    - Create `app/api/__init__.py` and `app/api/errors.py` translating service-layer `ValueError` and auth errors to the status/body table in the design (401/403/400/409/404/422/429/502)
    - _Requirements: 5.4, 5.8, 9.9, 12.3, 13.5, 15.9, 3.4_

  - [x] 4.2 Implement auth dependencies
    - Create `app/api/deps.py` with `get_current_user` (resolve Bearer/httpOnly-cookie token → hash → registry `Device` lookup → unexpired/unrevoked check → `AuthedUser`), `require_owner` (403 unless owner), and `get_tenant_db_for_user` (yield business-DB session scoped to the token-derived tenant); fail closed when role/tenant cannot be resolved
    - _Requirements: 2.10, 2.11, 5.6, 5.8, 19.2, 19.4, 19.5, 19.6_

  - [x] 4.3 Implement role-aware response schemas
    - Create `app/api/schemas.py` with Pydantic request/response models and serializer functions that take `AuthedUser` and omit cost/cost-per-unit/profit/financial fields for Staff
    - _Requirements: 5.4, 10.7, 11.7_

  - [x] 4.4 Implement the auth router
    - Create `app/api/auth_router.py` (prefix `/api/v1/auth`) wiring OTP request/verify, PIN set/verify, WebAuthn register/verify, Owner-only user list/create, and Manage_Mode elevation to `AuthService`/`WebAuthnService`
    - _Requirements: 2.1, 2.5, 4.1, 4.3, 4.6, 4.7, 5.5, 5.7, 6.3_

  - [x] 4.5 Add register_api() mount and HTTPS redirect
    - Add `register_api()` in `app/webhook_server.py` mounting the auth router and the `/app` static SPA (`app/webapp_static/`, `html=True`), extend CORS to the production origin, add `HTTPSRedirectMiddleware` as defense-in-depth, and call `register_api()` at startup from `app/telegram_listener.py` alongside `register_booth()`
    - _Requirements: 1.7, 1.8, 20.2_

  - [x] 4.6 Seed Owner users for existing tenants
    - Create `scripts/seed_owner_users.py` that creates one Owner `User` per existing tenant in the registry DB (phone from tenant chat context where available)
    - _Requirements: 5.1, 5.5_

  - [ ]* 4.7 Write property test — protected ops rejected before execution (hypothesis)
    - **Property 2: Protected operations are rejected for insufficient roles before execution**
    - **Validates: Requirements 5.4, 5.6, 5.7, 5.8, 9.6, 11.7, 14.6, 16.6, 19.4**

  - [ ]* 4.8 Write property test — no financial field in Staff responses (hypothesis)
    - **Property 3: No cost or financial field appears in any Staff-facing response**
    - **Validates: Requirements 10.7, 11.7**

  - [ ]* 4.9 Write property test — every authenticated request scoped to acting tenant (hypothesis)
    - **Property 4: Every authenticated request is scoped to the acting user's tenant**
    - **Validates: Requirements 19.1, 19.2, 19.3, 19.6**

  - [ ]* 4.10 Write property test — requests without valid identity are denied (hypothesis)
    - **Property 5: Requests without a valid authenticated identity are denied**
    - **Validates: Requirements 2.10, 2.11, 5.8, 19.4, 19.5**

  - [ ]* 4.11 Write manifest & HTTPS smoke tests (not PBT)
    - Smoke tests asserting the manifest fields, `/app` static mount serving, and HTTP→HTTPS redirect behavior
    - _Requirements: 1.1, 1.7, 1.8, 20.1_

- [ ] 5. Checkpoint — Phase 1 complete (auth + API foundation shippable)
  - Ensure all tests pass; verify `/api/v1/auth/*` works end to end and the booth + Telegram paths still function. Ask the user if questions arise.

- [ ] 6. Phase 2 — PWA shell and navigation
  - [x] 6.1 Scaffold the frontend build
    - Create `frontend/` with `package.json` (React + TypeScript + Ionic + Vite + Workbox + fast-check), `index.html`, and `vite.config.ts` configured to build into `app/webapp_static/` with the Workbox SW plugin
    - _Requirements: 20.1_

  - [x] 6.2 Add web app manifest and icons
    - Create `frontend/manifest.webmanifest` (name, start_url=`/app`, `display=standalone`) and `frontend/public/icons/` with 192x192 and 512x512 PNGs
    - _Requirements: 1.1, 1.6_

  - [x] 6.3 Implement the Workbox service worker
    - Create `frontend/src/sw.ts` precaching the app shell (HTML/CSS/JS/icons) and runtime-caching the product catalog; register it on first load and degrade gracefully with an offline-unavailable indication if registration/caching fails
    - _Requirements: 1.2, 1.3, 1.4, 18.1, 18.2_

  - [x] 6.4 Implement the API client
    - Create `frontend/src/api/client.ts` (attaches device token via httpOnly cookie preferred / Bearer fallback, 30s abort timeout, error surfacing that retains unsaved input) and `frontend/src/api/endpoints.ts` with typed per-domain calls
    - _Requirements: 20.2, 20.3_

  - [x] 6.5 Implement auth context and unlock UI
    - Create `frontend/src/auth/AuthContext.tsx` (device token, current user, role, Sell/Manage mode) plus `OtpFlow.tsx`, `PinPad.tsx`, `WebAuthnButton.tsx`, and `UserSwitcher.tsx`
    - _Requirements: 2.1, 4.1, 4.7, 4.9, 6.3, 6.4, 6.5_

  - [x] 6.6 Implement role/mode-aware tab bar
    - Create `frontend/src/tabs/TabBar.tsx` rendering exactly the 8 tabs (Sell, Orders, Inventory, Recipes, Customers, Invoices, Expenses, Ask/Insights) and filtering visibility by role/mode from `AuthContext` (Staff/Sell_Mode hides Expenses and Owner-only surfaces)
    - _Requirements: 1.9, 1.10, 6.1, 6.2, 14.6_

  - [ ]* 6.7 Write property test — navigation visibility equals permitted surfaces (fast-check)
    - **Property 1: Navigation visibility equals permitted surfaces for role and mode**
    - **Validates: Requirements 1.9, 1.10, 6.1, 6.2, 14.6**

  - [ ]* 6.8 Write PWA runtime tests (Playwright, not PBT)
    - Browser tests for service-worker registration, offline shell load, install prompt presence, and standalone launch
    - _Requirements: 1.2, 1.3, 1.4, 1.5, 1.6, 18.1, 18.2_

- [ ] 7. Phase 2 — Sell surface, attribution, and offline
  - [x] 7.1 Implement the sell router
    - Create `app/api/sell_router.py` (prefix `/api/v1/sell`) over `BoothService`: session/items, checkout that sets `Order.created_by_user_id` from `AuthedUser` inside the same transaction, `Idempotency-Key` handling via `SellIdempotency` (return existing order on duplicate), and receipt/invoice endpoints
    - _Requirements: 7.1, 7.4, 7.5, 8.1, 8.3, 8.4, 8.5, 8.6, 8.7, 8.8, 18.5_

  - [x] 7.2 Implement the Sell tab UI
    - Create `frontend/src/tabs/Sell.tsx`: product grid from cached catalog, cart with sub-1s total updates, Cash/UPI selection, checkout, and receipt/invoice access
    - _Requirements: 8.1, 8.2, 8.4, 8.5_

  - [x] 7.2a Always-on Sell session + Owner catalog setup
    - `app/api/sell_router.py`: add `_ensure_session_synced` (auto-start an always-on "Sell" session and sync every catalog variant with unlimited stock `stock_qty=None`), rewire `GET /session` to sync first, and add Owner-only `POST /api/v1/sell/products` ("Add item"). Frontend `Sell.tsx`: Owner-only "Add item" + "Upload catalog" (reuses ingestion `doc_type="catalog"` confirm/edit); Staff hides both. Tests in `tests/test_sell_setup.py`
    - _Requirements: 8.1, 8.9, 8.10, 8.11, 8.12_

  - [x] 7.2c OTP delivery over Telegram + first-run per-tab tour
    - OTP-in-Telegram (Req 3.1): fix `RegistryChannelResolver` to match a stored `+<digits>` phone (was stripping `+` → always SMS fallback); add `set_telegram_otp_send` in `auth_router` and register the running bot's Bot-API send (`TelegramBotListener._send_otp_message`) at webhook startup, so a known owner's sign-in code is delivered over their Telegram channel (SMS stays fallback). Tests in `tests/test_otp_channel_resolver.py`.
    - First-run tour (Req 21): `frontend/src/components/TabTour.tsx` shows a one-time info popup per tab, once per user account (localStorage keyed by user id); wired into all 8 tabs with tab-specific copy.
    - Clickable app link: bot welcome + existing-owner messages now use a Markdown link for `APP_URL`.
    - _Requirements: 3.1, 3.2, 21.x_

  - [x] 7.2b Session restore, fresh-read caching, and Sell checkout tax
    - Add `GET /api/v1/auth/session` returning the current user for a valid device token; `AuthContext` rehydrates it on startup so a reload reopens without re-verifying OTP (Req 2.10), with a Gate loader to avoid flashing the sign-in screen. Switch the service worker's `/api/v1/*` GET runtime cache from StaleWhileRevalidate to **NetworkFirst** so every tab shows current data immediately after a write (still offline-capable). Add an optional GST% input to Sell checkout, sending `gst_rate` on the checkout body (Req 8.6). Tests in `tests/test_auth_router.py`
    - _Requirements: 2.10, 8.6, 18.1, 18.2_

  - [x] 7.3 Implement offline cart queue and sync
    - Create `frontend/src/offline/cartQueue.ts` (IndexedDB queue of offline sales with per-sale idempotency keys) and `frontend/src/offline/sync.ts` (drain on reconnect within 30s, at-most-once, ≤3 retries, report+retain permanent failures)
    - _Requirements: 18.3, 18.4, 18.5, 18.6_

  - [ ]* 7.4 Write property test — attribution immutability (hypothesis)
    - **Property 15: A completed sale is attributed to its acting user, immutably**
    - **Validates: Requirements 7.1, 7.3, 7.4, 7.5**

  - [ ]* 7.5 Write property test — checkout cardinality and atomicity (hypothesis)
    - **Property 16: Checkout is all-or-nothing and creates exactly one order and one payment**
    - **Validates: Requirements 8.3, 8.4, 8.7, 8.8**

  - [ ]* 7.6 Write property test — offline sync at most once (fast-check)
    - **Property 18: Offline sales sync at most once with bounded retries**
    - **Validates: Requirements 18.3, 18.5, 18.6**

  - [ ]* 7.7 Write property test — cart total equals sum of line amounts (fast-check)
    - **Property 17: Cart total equals the sum of line amounts**
    - **Validates: Requirements 8.2**

- [ ] 8. Phase 2 — Orders surface
  - [x] 8.1 Implement the orders router
    - Create `app/api/orders_router.py` (prefix `/api/v1/orders`) over `OrderService`: create (pending), list with date/status filter, deliver, cancel (retain), Owner-only delete, with a status-transition guard rejecting illegal transitions and required-field/range validation
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7, 9.8, 9.9_

  - [x] 8.2 Implement the Orders tab UI
    - Create `frontend/src/tabs/Orders.tsx`: create form, status display, deliver/cancel actions, date/status filters, Owner-only delete affordance
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6_

  - [ ]* 8.3 Write property test — order creation validation (hypothesis)
    - **Property 19: Order creation validates required fields and value ranges**
    - **Validates: Requirements 9.1, 9.7, 9.8**

  - [ ]* 8.4 Write property test — order status state machine (hypothesis)
    - **Property 20: Order status transitions follow the allowed state machine**
    - **Validates: Requirements 9.3, 9.4, 9.9**

  - [ ]* 8.5 Write property test — order filtering (hypothesis)
    - **Property 21: Order filtering returns exactly the matching orders**
    - **Validates: Requirements 9.5**

- [ ] 9. Phase 2 — Inventory surface
  - [x] 9.1 Implement the inventory router
    - Create `app/api/inventory_router.py` (prefix `/api/v1/inventory`) over `InventoryService`: create/update with field-range validation, a grouped-and-alphabetically-sorted list serializer, and Staff cost-field stripping
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.7_

  - [x] 9.2 Implement the Inventory tab UI
    - Create `frontend/src/tabs/Inventory.tsx`: category-grouped list, empty-state indication, create/update forms, cost hidden for Staff
    - _Requirements: 10.5, 10.6, 10.7_

  - [ ]* 9.3 Write property test — inventory round-trip and validation (hypothesis)
    - **Property 22: Inventory create/update round-trips valid values and rejects invalid ones**
    - **Validates: Requirements 10.1, 10.2, 10.3, 10.4**

  - [ ]* 9.4 Write property test — inventory grouping and ordering (hypothesis)
    - **Property 23: Inventory listing is grouped and alphabetically ordered**
    - **Validates: Requirements 10.5**

- [ ] 10. Phase 2 — Recipes surface
  - [x] 10.1 Implement the recipes router
    - Create `app/api/recipes_router.py` (prefix `/api/v1/recipes`) over `RecipeService`: create recipe, add component, list (cost-per-unit omitted for Staff), and an Owner-only `GET /{id}/cost` endpoint
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5, 11.6, 11.7_

  - [x] 10.2 Implement the Recipes tab UI
    - Create `frontend/src/tabs/Recipes.tsx`: recipe create + component add forms, recipe list, cost-per-unit shown only for Owner
    - _Requirements: 11.1, 11.2, 11.4, 11.7_

  - [ ]* 10.3 Write property test — recipe cost-per-unit formula (hypothesis)
    - **Property 24: Recipe cost-per-unit equals the component-cost formula**
    - **Validates: Requirements 11.3, 11.4**

  - [ ]* 10.4 Write property test — recipe/component validation (hypothesis)
    - **Property 25: Recipe and component creation validate names, yields, and quantities**
    - **Validates: Requirements 11.1, 11.2, 11.5, 11.6**

- [ ] 11. Phase 2 — Customers surface
  - [x] 11.1 Implement the customers router
    - Create `app/api/customers_router.py` (prefix `/api/v1/customers`) over `CustomerService`: create with name/phone validation and per-tenant phone uniqueness conflict, and search capped at 50 results
    - _Requirements: 12.1, 12.2, 12.3, 12.4_

  - [x] 11.2 Implement the Customers tab UI
    - Create `frontend/src/tabs/Customers.tsx`: create form, search-as-you-type with sub-2s results, empty-result indication
    - _Requirements: 12.1, 12.2, 12.5_

  - [ ]* 11.3 Write property test — customer validation and phone uniqueness (hypothesis)
    - **Property 26: Customer creation enforces field validity and phone uniqueness per tenant**
    - **Validates: Requirements 12.1, 12.3, 12.4**

  - [ ]* 11.4 Write property test — customer search cap (hypothesis)
    - **Property 27: Customer search returns only matching results, capped at 50**
    - **Validates: Requirements 12.2**

- [ ] 12. Phase 2 — Invoices surface
  - [x] 12.1 Implement the invoices router
    - Create `app/api/invoices_router.py` (prefix `/api/v1/invoices`) over `InvoiceService.build_invoice_data/generate`: generate PDF invoice with business/customer/items/subtotal/tax/total in tenant currency, optional GST breakdown, order-not-found and future-delivery-date rejections
    - _Requirements: 13.1, 13.2, 13.3, 13.4, 13.5, 13.6, 13.7_

  - [x] 12.2 Implement the Invoices tab UI
    - Create `frontend/src/tabs/Invoices.tsx`: select an order, request invoice, display/download the generated PDF with tenant-currency values
    - _Requirements: 13.1, 13.3_

  - [ ]* 12.3 Write property test — invoice data completeness and taxing (hypothesis)
    - **Property 28: Invoice data is complete, correctly taxed, and in tenant currency**
    - **Validates: Requirements 13.1, 13.2, 13.3, 13.4, 13.7**

  - [ ]* 12.4 Write property test — future delivery date rejected (hypothesis)
    - **Property 29: Invoice requests with a future delivery date are rejected**
    - **Validates: Requirements 13.6**

- [ ] 13. Phase 2 — Expenses surface
  - [x] 13.1 Implement the expenses router
    - Create `app/api/expenses_router.py` (prefix `/api/v1/expenses`, Owner-only) over the expense service-layer path: create with amount/category/description validation and capital-asset flag, and list with category + inclusive date-range filter rejecting inverted ranges
    - _Requirements: 14.1, 14.2, 14.3, 14.4, 14.5, 14.6_

  - [x] 13.2 Implement the Expenses tab UI
    - Create `frontend/src/tabs/Expenses.tsx`: create form with predefined categories and capital flag, category + date-range filter, invalid-range error handling (Owner-only surface)
    - _Requirements: 14.1, 14.3, 14.4, 14.5, 14.6_

  - [ ]* 13.3 Write property test — expense validation and capital flag (hypothesis)
    - **Property 30: Expense creation validates fields and preserves the capital flag**
    - **Validates: Requirements 14.1, 14.2, 14.3**

  - [ ]* 13.4 Write property test — expense date-range filtering (hypothesis)
    - **Property 31: Expense date-range filtering returns only in-range matching records**
    - **Validates: Requirements 14.4, 14.5**

- [ ] 14. Checkpoint — Phase 2 complete (all deterministic tabs shippable)
  - Ensure all tests pass; verify each tab operates against its service layer and existing booth/Telegram tests remain green. Ask the user if questions arise.

- [ ] 15. Phase 3 — Image ingestion (confirm-and-edit, no chat)
  - [x] 15.1 Implement the ingestion router
    - Create `app/api/ingestion_router.py` (prefix `/api/v1/ingestion`): `POST /extract` (validate ≤10 MB + supported format, call `ImageService.process_*`, shape a typed `Ingestion_Draft`, 422 on extraction failure) and `POST /confirm` (route receipt→expense, recipe→recipe+component, order→order, catalog→product, payment→payment; persist nothing on domain failure)
    - _Requirements: 15.1, 15.4, 15.5, 15.6, 15.8, 15.9, 15.10_

  - [x] 15.2 Implement the ingestion draft forms UI
    - Add ingestion form components under `frontend/src/tabs/` that render each `Ingestion_Draft` as an editable form (not a chat reply), allow editing any field, and support confirm/discard (discard is client-side only)
    - _Requirements: 15.2, 15.3, 15.6, 15.7_

  - [ ]* 15.3 Write property test — ingestion validation and confirm routing (hypothesis)
    - **Property 32: Image ingestion validates input and confirm routes to the matching domain create**
    - **Validates: Requirements 15.4, 15.5, 15.8, 15.10**

  - [ ]* 15.4 Write image extraction example tests (not PBT)
    - 1–3 example tests per document type using recorded/mocked vision responses for successful extraction and extraction failure
    - _Requirements: 15.1, 15.9_

- [ ] 16. Phase 4 — Ask / Insights
  - [x] 16.1 Implement the insights router
    - Create `app/api/insights_router.py` (prefix `/api/v1/insights`): `POST /ask` routed through the read-only agent tool allowlist / `ReportingService`, scoped to the token-derived tenant, blocking financial (revenue/cost/profit) queries for Staff, validating reporting periods (start ≤ end, present), computing order cost as sum(quantity × recorded unit price) and reporting ingredients missing a unit price
    - _Requirements: 16.1, 16.2, 16.3, 16.4, 16.5, 16.6, 16.7_

  - [x] 16.2 Implement the Insights tab UI
    - Create `frontend/src/tabs/Insights.tsx`: question input, answer display, missing/invalid-period messaging, financial questions hidden/blocked for Staff
    - _Requirements: 16.1, 16.3, 16.6_

  - [ ]* 16.3 Write property test — financial aggregation over inclusive period (hypothesis)
    - **Property 33: Financial analysis aggregates over the inclusive period and validates it**
    - **Validates: Requirements 16.2, 16.3, 16.4, 16.5**

  - [ ]* 16.4 Write insights latency/answerability example tests (not PBT)
    - Example tests with representative questions asserting tenant-scoped answers and unanswerable-question messaging
    - _Requirements: 16.1, 16.7_

- [ ] 17. Phase 5 — Notifications demotion
  - [x] 17.1 Implement outbound notification triggers
    - Create `app/notifications/__init__.py` and `app/notifications/notifier.py` with order-alert, Instagram-order-alert, subscription-expiry-warning (once per calendar day in the 7-day window), and daily-summary senders over the tenant's configured `messaging_platform`, with delivery retry up to 3 attempts and failure recording; leave the existing agent command path intact
    - _Requirements: 17.1, 17.2, 17.3, 17.4, 17.6, 17.7_

  - [ ]* 17.2 Write property test — notification fields and retry bound (hypothesis)
    - **Property 34: Notification messages contain the required fields and retry at most three times**
    - **Validates: Requirements 17.1, 17.2, 17.3, 17.4, 17.7**

  - [ ]* 17.3 Write notification command-path example tests (not PBT)
    - Example tests asserting a text command over the channel routes through the existing agent path and an unprocessable command returns a not-understood error over the same channel
    - _Requirements: 17.5, 17.8_

- [ ] 18. Final integration and wiring
  - [x] 18.1 Wire all domain routers into register_api()
    - Update `register_api()` in `app/webhook_server.py` to `include_router` for sell, orders, inventory, recipes, customers, invoices, expenses, insights, and ingestion routers (each with its `/api/v1/...` prefix), completing the additive mount
    - _Requirements: 20.2, 20.4_

  - [ ]* 18.2 Write service-equivalence property test and regression guard (hypothesis)
    - **Property 35: App domain operations return results identical to the underlying service layer**
    - **Validates: Requirements 8.6, 20.4, 20.5**
    - Also run the existing booth/Telegram suites (`tests/test_booth_service.py`, etc.) to confirm unchanged service-layer behavior
    - _Requirements: 20.4, 20.5_

- [ ] 19. Final checkpoint — all phases complete
  - Ensure all tests pass across backend and frontend, confirm the Telegram poller + booth router still operate, and confirm `register_api()` is additive and revertible. Ask the user if questions arise.

- [x] 20. Post-MVP additions (implemented beyond the original plan)
  - These items were built after the original plan and are reflected here as done/current behavior.
  - [x] 20.1 In-context inventory scan ingestion + Inventory-tab UI
    - Add ingestion doc-type `inventory` routing confirmed drafts to `InventoryService.create_item` (persist nothing on failure), and add a "Scan receipt" action with a confirm-and-edit form to `frontend/src/tabs/Inventory.tsx` (camera capture or file upload); doc-type is fixed by the launch context (no classification)
    - _Requirements: 10.8, 10.9, 15.11, 15.12, 15.13_
  - [x] 20.2 In-context recipe scan UI on the Recipes tab
    - Add a "Scan recipe" action to `frontend/src/tabs/Recipes.tsx` that launches the existing recipe ingestion in-context (`doc_type=recipe`) with a confirm-and-edit form creating a recipe + components
    - _Requirements: 11.8, 11.9, 15.11, 15.12_
  - [x] 20.3 Insights agent chat endpoint + chat UI
    - Add `POST /api/v1/insights/chat` running `AgentService` (Plan→Execute→Summarise) with the full `ToolExecutor` and tenant-scoped DB access, raising `AgentUnavailableError` (502) on LLM failure; convert `frontend/src/tabs/Insights.tsx` to a chat UI with no required date fields; retain the deterministic `/ask` endpoint
    - _Requirements: 16.1, 16.2, 16.4, 16.5, 16.6, 16.7_
  - [x] 20.4 Owner onboarding: Owner-User creation + auto-trial + PWA link
    - In the bot onboarding flow, create an Owner `User` bound to the tenant with the captured phone (idempotent), auto-start a 7-day trial (replacing the manual-approval gate), and send the PWA link (`settings.APP_URL`) with add-to-home-screen + sign-in guidance; on WhatsApp reuse a valid-phone `chat_id` and skip the phone question
    - _Requirements: 21.1, 21.2, 21.3, 21.4, 21.7_
  - [x] 20.5 register_api error-handler registration
    - Register the shared API error handlers (`app/api/errors.py`) in `register_api()` so service-layer and auth errors map to the documented HTTP responses
    - _Requirements: 5.4, 9.9, 12.3, 15.9_
  - [x] 20.6 Local dev runner + HTTPS redirect config
    - Add the `ENABLE_HTTPS_REDIRECT` config gating `HTTPSRedirectMiddleware` (off for local HTTP dev) and `scripts/run_local.py` as a local dev runner
    - _Requirements: 1.7, 1.8, 20.2_

- [ ] 21. Phase 6 — Subscription & Payments (TODO, not yet implemented)
  - Future/not-started. Post-trial billing so expired tenants can pay to continue. The gateway integration reuses the existing booth Razorpay patterns.
  - [ ] 21.1 Integrate Razorpay subscription/payment for post-trial billing
    - Reuse `app/booth/razorpay_client.py` patterns to create a subscription/order or payment link when a trial expires
    - _Requirements: 21.2, 21.6_
  - [ ] 21.2 Add a payment webhook
    - Add a webhook endpoint that verifies the gateway signature and, on successful payment, calls `TenantService.activate_subscription` to move the tenant to active
    - _Requirements: 21.6_
  - [ ] 21.3 Pay-to-continue link on expiry
    - When a tenant's status is expired, send the Owner a pay-to-continue link over the bot and surface it in-app; unblock App access on successful payment
    - _Requirements: 21.2, 21.6_
  - [ ] 21.4 Admin extended/complimentary trial override
    - Document the extended/complimentary trial override (largely exists via `/trial <chat_id> [days]`) and add tests covering admin start/extend
    - _Requirements: 21.5_
  - [ ]* 21.5 Property/integration tests for the payment lifecycle (gateway mocked)
    - Cover the trial→expired→paid→active lifecycle end to end with the payment gateway mocked, asserting `activate_subscription` is invoked only on a verified successful payment and access is unblocked accordingly
    - _Requirements: 21.2, 21.6_

- [ ] 22. Phase 7 — UI theming & visual polish (TODO, not yet implemented)
  - Future/not-started. The app currently uses default Ionic styling with no brand theme, so buttons/screens look plain. This phase applies a KitchenOS brand theme and polish pass. No functional/behavioral change.
  - [ ] 22.1 Apply a KitchenOS brand theme
    - Define brand palette + typography via Ionic CSS variables in `frontend/src/theme/variables.css` (primary/secondary/tertiary, success/warning/danger, light/dark), and a shared header/toolbar treatment
    - [ ] 22.2 Polish core surfaces (Sell grid, forms, empty states)
    - Restyle the Sell product grid as cards, standardize form/modal spacing, and give each tab a branded empty state and loading skeleton
    - [ ] 22.3 App icon, splash, and install polish
    - Provide branded PWA icons/splash in the manifest and verify the install/home-screen presentation

## Notes

- Tasks marked with `*` are optional test sub-tasks and can be skipped for a faster MVP; core implementation sub-tasks are never optional.
- The task order mirrors the design's phased, Telegram-safe rollout so the system is shippable at every checkpoint.
- Backend property tests use `hypothesis`; frontend logic property tests (Properties 1, 17, 18) use `fast-check`. Each property test is tagged `# Feature: app-first-pivot, Property {n}` and runs a minimum of 100 iterations with external senders and the vision LLM mocked.
- Non-PBT concerns (PWA runtime, WebAuthn ceremonies, image extraction, migration, notification command path, manifest/HTTPS) are covered by unit/integration/smoke tests per the design's Testing Strategy.
- The existing service layer is reused unchanged; new routers are thin HTTP adapters, and `register_api()` is an additive mount that can be disabled to roll back to chat+booth behavior.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2", "3.1", "4.1"] },
    { "id": 1, "tasks": ["1.3", "3.2", "3.3", "3.6", "4.2", "4.3", "6.1"] },
    { "id": 2, "tasks": ["1.4", "3.4", "3.7", "3.8", "3.9", "3.10", "3.11", "3.16", "6.2", "6.3", "6.4", "7.1", "8.1", "9.1", "10.1", "11.1", "12.1", "13.1", "15.1", "16.1", "17.1"] },
    { "id": 3, "tasks": ["3.5", "3.12", "3.13", "6.5", "7.3", "7.4", "7.5", "8.3", "8.4", "8.5", "9.3", "9.4", "10.3", "10.4", "11.3", "11.4", "12.3", "12.4", "13.3", "13.4", "15.3", "15.4", "16.3", "16.4", "17.2", "17.3"] },
    { "id": 4, "tasks": ["3.14", "3.15", "4.4", "6.6", "7.2", "7.6", "8.2", "9.2", "10.2", "11.2", "12.2", "13.2", "15.2", "16.2"] },
    { "id": 5, "tasks": ["4.5", "4.6", "4.7", "4.8", "4.9", "4.10", "6.7", "7.7"] },
    { "id": 6, "tasks": ["4.11", "6.8", "18.1"] },
    { "id": 7, "tasks": ["18.2"] }
  ]
}
```
