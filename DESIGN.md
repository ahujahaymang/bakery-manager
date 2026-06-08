# KitchenOS — Design Document

## What It Is

A conversational AI assistant delivered over Telegram. Users send plain-language messages — "create order for Priya, 2 brownies at ₹150, deliver May 10" — and the bot understands, acts, and responds. No forms, no menus, no commands to memorise.

Built as a multi-tenant SaaS platform: one deployment serves many independent businesses, each with fully isolated data.

---

## Capabilities

### Onboarding
- First message triggers a two-step setup: business name → country
- Country determines the currency used on invoices (India → ₹, US → $, UK → £, etc.)
- Account is pending admin approval after onboarding — admin gets notified immediately
- Admin approves via `/trial <chat_id>` (7-day free trial) or `/approve <chat_id> [days]`
- Owner receives a confirmation message when approved

### Inventory
- Add ingredients and packaging materials with quantity, unit, and cost
- Update stock levels and costs
- View all inventory grouped by category

### Recipes
- Create recipes with yield per batch
- Add ingredients and packaging as components with quantities
- Calculate cost per unit automatically
- Edit, rename, or delete recipes
- Upload a photo of a handwritten recipe — the bot reads it and creates the recipe

### Customers
- Add customers with name and phone number
- Search by name or phone

### Orders
- Create orders for customers with multiple items, quantities, and selling prices
- Ask for delivery date if not provided
- Mark orders as delivered
- Cancel orders (keeps record for analysis)
- Delete orders entered by mistake
- View upcoming orders, filter by paid/unpaid/delivered/pending

### Payments
- Record payments against orders (Cash, UPI, Razorpay, Bank Transfer)
- View payment history with date filters
- See all orders with outstanding balances

### Invoices
- Generate a PDF invoice for any order with one message
- Invoice includes business name, customer details, itemised list, totals, amount due
- Supports GST/tax with configurable rate and label
- Supports backdated delivery dates for historical orders
- Currency shown based on the business's country setting

### Register Mode (web UI)
- Mobile-first sell screen — tap products, checkout, print receipt
- Owner opens `/register/{tenant_id}` — bookmarkable, no app required
- Two modes: **Regular Day** (open-ended) and **Event/Booth** (fixed duration, auto-ends)
- Setup from the web UI: multi-select products, set prices, add custom items
- Checkout: per-item notes/customisation charges, GST field
- Receipt: prints to thermal printer via browser print
- Invoice button: generates PDF with GST, downloads immediately
- Orders view: see all session orders with detail
- Telegram just generates the link — all product selection happens in the web app

### Expense Tracking
- Record any business expense: ingredients, equipment, utilities, rent, marketing, etc.
- Capital asset tracking (`is_capital=true` for ovens, mixers, stands)
- Filter by category, date range, capital-only
- Triggered automatically from receipt images

### Subscription & Access Control
- New users complete onboarding then wait for admin approval
- `/trial <chat_id> [days]` — start free trial (default 7 days)
- `/approve <chat_id> [days]` — activate paid subscription
- `/status` — view all tenants with subscription status and days remaining
- Access blocked with friendly message when trial/subscription expires
- 2-day warning injected into conversation before expiry

### Reports
- Weekly profit breakdown: revenue, ingredient cost, packaging cost, gross profit

### Image Processing
- Send a photo of a handwritten recipe → bot creates the recipe
- Send a purchase receipt → bot updates inventory
- Send a customer payment receipt → bot records the payment
- Send a product catalog photo → bot imports all products
- Send a WhatsApp/SMS order screenshot → bot creates the order

### Commands
- `/clear` — wipe conversation history (also `/reset`, `/start over`)
- `/report` — re-send last error to admin
- `/feedback <text>` — send feedback
- `/request <text>` — submit a feature request
- `/privacy` — show privacy policy

---

## How It Works

### For the user

The user messages the Telegram bot in plain language. The bot understands the intent, performs the action, and replies — all in one turn.

### Under the hood

```
User message
     │
     ▼
Telegram Listener          ← receives message, resolves tenant
     │
     ▼
Request Handler            ← manages conversation history, onboarding, commands
     │
     ▼
LLM Agent (GPT-4o mini)    ← Plan → Execute → Summarise (max 2 LLM calls/turn)
     │
     ▼
Tool Executor              ← calls the right service method
     │
     ▼
Service Layer              ← business logic, database operations
     │
     ▼
Database (SQLite / PostgreSQL)
```

The LLM agent uses **tool calling** with a fixed 2-call pattern:
1. **Plan** — LLM decides which tool(s) to call
2. **Execute** — all tools run in parallel (no LLM involved)
3. **Summarise** — LLM formats the results into a user response

This means max 2 LLM calls per turn regardless of how many tools are needed.

---

## Architecture

### Components

| Component | File | Responsibility |
|---|---|---|
| Telegram Adapter | `app/telegram_listener.py` | Receive messages, download photos, send replies |
| Request Handler | `app/handlers/request_handler.py` | Conversation history, image processing, admin routing, metrics |
| Agent | `app/services/agent_service.py` | Plan→Execute→Summarise with tool calling |
| Tool Executor | `app/services/tool_executor.py` | Maps tool names to service method calls |
| Services | `app/services/*.py` | Business logic, one file per domain |
| Register/Booth | `app/booth/` | Mobile web sell screen (FastAPI + Vanilla JS) |
| Models | `app/models.py` | Database schema (SQLAlchemy ORM) |
| Config | `app/config.py` | Environment-based configuration |
| Metrics | `app/services/metrics_service.py` | In-process observability (LLM cost, latency, errors) |

### Multi-tenancy

Every database record includes a `tenant_id`. All service queries filter by `tenant_id` — one tenant can never see another's data. A new tenant is created automatically the first time a user messages the bot.

### Database

Supports two engines, switchable via `DB_ENGINE` environment variable:

| | SQLite (per-tenant files) | PostgreSQL |
|---|---|---|
| Use case | Shared server, many tenants | High-volume or compliance needs |
| Cost | ~$0/month (files on disk) | ~$12/month (RDS db.t4g.micro) |
| Isolation | Separate `.db` file per tenant | Shared DB, isolated by `tenant_id` column |
| Backups | Hourly per-tenant snapshots to S3 | Automated by RDS |

**SQLite per-tenant layout:**
```
/data/
    tenants.db                    ← shared registry (chat_id → tenant_id)
    <tenant_id>.db                ← Priya's bakery data
    <tenant_id>.db                ← Raj's bakery data
    ...
```

### Conversation history

The last **8 message turns** are persisted to the DB via `ConversationService` and loaded on every request. History survives bot restarts. The in-memory `_history` dict is kept only as a lightweight cache for onboarding state checks.

---

## Data Model

```
Tenant ──┬── Customer ──── Order ──┬── OrderItem ──── Recipe ──── RecipeComponent
         │                │         └── Payment                        │
         │                └── BoothSession ──── BoothSessionItem       │
         ├── InventoryItem ──────────────────────────────────────────────┘
         ├── PurchaseExpense
         ├── Product ──── ProductVariant
         └── AuditLog
```

- **Tenant**: one per chat_id; stores business name, country, subscription status, Razorpay keys
- **Customer**: name + phone, unique per tenant
- **InventoryItem**: ingredient or packaging, with quantity, unit, cost
- **Recipe**: name + yield per batch, linked to inventory items via components
- **Order**: customer + delivery date + status (pending/delivered/cancelled); nullable `booth_session_id`
- **OrderItem**: recipe name + quantity + selling price + optional customization charge/note
- **Payment**: amount + method + status; nullable `razorpay_payment_id`
- **Product / ProductVariant**: sellable items with size labels and prices (used by Register Mode)
- **BoothSession**: Register Mode session with mode (regular/event), duration, and end time
- **BoothSessionItem**: product variant + booth price + stock/sold qty per session
- **PurchaseExpense**: business expenses with category, capital flag, description
- **AuditLog**: records all UPDATE/DELETE operations

---

## Observability

### Metrics endpoint

`GET /metrics?hours=24&key=<METRICS_KEY>` returns:

```json
{
  "users": { "active_24h": 5, "active_7d": 12 },
  "requests": { "total": 143, "latency_p50_ms": 1800, "latency_p95_ms": 4200 },
  "llm": {
    "total_calls": 280,
    "total_cost_usd": 0.0312,
    "cost_per_active_user_usd": 0.0062,
    "by_model": { "gpt-4o-mini": { "calls": 280, "cost_usd": 0.0312 } }
  },
  "errors": { "total": 3, "by_type": { "ValueError": 2, "TimeoutError": 1 } },
  "tools": { "top": [{ "name": "create_order", "calls": 45 }] }
}
```

Costs are estimated from published token pricing and reset on restart. For persistent metrics, enable CloudWatch metric filters on the `METRIC` structured log lines emitted by every LLM call and request.

### CloudWatch

Application logs go to `/kitchenos/prod/app`. The `METRIC` prefix on structured log lines can be used as CloudWatch metric filter patterns to build dashboards and alarms.

---

## Register Mode (Booth)

The `/register/{tenant_id}` web app is a zero-LLM, mobile-first sell screen:

- Session created from Telegram (`create_booth_from_categories`) or directly on the web page
- Two modes: **Regular** (open-ended) and **Event** (fixed days, auto-expires)
- Checkout: per-item extra charges (packaging/customisation), GST field
- After checkout: Receipt (thermal print) or Invoice (PDF download)
- Orders history view in the header
- `/booth/{tenant_id}` remains a backward-compatible alias

---

## Admin Access

Set `ADMIN_CHAT_ID` to your Telegram chat_id. As admin:
- Skip the welcome screen — operate on owner data directly
- `/status` — lists all tenants with subscription status
- `/switch [chat_id]` — list or switch active tenant
- `/trial <chat_id> [days]` — start/reset trial
- `/approve <chat_id> [days]` — activate paid subscription
- `/delete <chat_id>` — permanently delete a tenant's data

---

## Data Security

- All data encrypted at rest (EBS AES-256)
- All data encrypted in transit (TLS via nginx + Let's Encrypt on kitchenos.info)
- No public ports open on EC2 — SSH via SSM Session Manager only
- Secrets stored in AWS SSM Parameter Store (KMS encrypted)
- OpenAI API: inputs not used for training; retained max 30 days
- Metrics endpoint protected by `METRICS_KEY`

---

## Technology Stack

| Layer | Technology |
|---|---|
| Language | Python 3.11+ |
| Messaging | python-telegram-bot (polling mode) |
| LLM — Agent | OpenAI GPT-4o mini (active), Amazon Bedrock Nova Lite (available) |
| LLM — Images | OpenAI GPT-4o mini (vision) |
| Web UI | FastAPI + Vanilla JS (Register Mode) |
| PDF generation | ReportLab |
| ORM | SQLAlchemy 2.0 |
| Database | SQLite per-tenant (production) or PostgreSQL |
| Infrastructure | AWS CDK (TypeScript) |
| Compute | EC2 t3.micro |
| Backup | S3 hourly SQLite snapshots |
| Domain / TLS | kitchenos.info (IONOS) + nginx + Let's Encrypt |
| Config | pydantic-settings |

---

## Project Structure

```
app/
├── booth/                          ← Register Mode web app
│   ├── router.py                   ← FastAPI routes (/register/ and /booth/ alias)
│   ├── booth_service.py            ← Session, checkout, reporting logic
│   ├── razorpay_client.py          ← Razorpay API wrapper
│   ├── static/                     ← JS + CSS (api.js, app.js, cart.js, checkout.js, ...)
│   └── templates/                  ← booth.html, receipt.html
├── handlers/
│   └── request_handler.py          ← Agent runner, history, onboarding, admin, metrics
├── services/
│   ├── agent_service.py            ← Plan→Execute→Summarise agent loop
│   ├── metrics_service.py          ← In-process observability singleton
│   ├── tool_executor.py            ← Tool name → service method mapping
│   ├── conversation_service.py     ← Persisted conversation history
│   ├── customer_service.py
│   ├── inventory_service.py
│   ├── order_service.py
│   ├── payment_service.py
│   ├── recipe_service.py
│   ├── reporting_service.py
│   ├── product_service.py
│   ├── tenant_service.py
│   ├── image_service.py
│   ├── invoice_service.py
│   ├── backup_service.py
│   └── llm_service.py              ← Image vision extraction (GPT-4o mini)
├── tools/
│   ├── __init__.py                 ← Assembles TOOLS + SYSTEM_PROMPT
│   ├── _base.py                    ← Tool definition helpers
│   ├── customer_tools.py
│   ├── inventory_tools.py
│   ├── order_tools.py
│   ├── payment_tools.py
│   ├── product_tools.py
│   ├── recipe_tools.py
│   ├── report_tools.py
│   └── other_tools.py              ← Expenses, Register Mode, templates
├── bedrock_client.py
├── llm_client.py
├── config.py
├── database.py
├── models.py
├── error_handler.py
├── telegram_listener.py
├── instagram_listener.py
└── webhook_server.py               ← FastAPI app, /health, /metrics, register routes
```

---

## Future Work

| Feature | Notes |
|---|---|
| **Razorpay payment confirmation** | Webhook handler built; needs end-to-end test with live keys |
| **Elastic IP** | Prevents IP change on EC2 restart (deferred) |
| **Dynamic DNS update** | `scripts/update_dns.sh` built; dormant until IONOS API keys added |
| **Register Mode PIN** | Short PIN per tenant to restrict booth URL access |
| **CloudWatch metric filters** | Build dashboards on the `METRIC` log lines |
| **Low stock alerts** | Notify owner via Telegram when booth product hits 0 remaining |
| **Multi-day event per-day breakdown** | Sales split by day in orders view |
| **GST export sheet** | GST-ready CSV for Indian businesses |
| **WhatsApp support** | RequestHandler is platform-agnostic; adapter only needed |
| **Multi-user access** | Role-based permissions for staff |
