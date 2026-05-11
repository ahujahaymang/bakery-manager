# Operations Bot — Design Document

## What It Is

A conversational AI assistant delivered over Telegram. Users send plain-language messages — "create order for Priya, 2 brownies at ₹150, deliver May 10" — and the bot understands, acts, and responds. No forms, no menus, no commands to memorise.

Built as a multi-tenant SaaS platform: one deployment can serve many independent businesses, each with fully isolated data.

---

## Capabilities

### Onboarding
- First message triggers a two-step setup: business name → country
- Country determines the currency used on invoices (India → Rs., US → $, UK → £, etc.)
- Account is pending admin approval after onboarding — admin gets notified immediately
- Admin approves via `/trial <chat_id>` (7-day free trial) or `/approve <chat_id> [days]`
- Owner receives a confirmation message when approved, including full capabilities overview

### Instagram Integration *(coming soon)*
- Owner connects their Instagram Business account once
- KitchenOS monitors Instagram DMs automatically
- LLM detects confirmed orders from customer conversations
- Owner gets a Telegram notification to confirm before order is created

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
- Record payments against orders (Cash, Paytm, Bank Transfer)
- View payment history with date filters
- See all orders with outstanding balances

### Invoices
- Generate a PDF invoice for any order with one message
- Invoice includes business name, customer details, itemised list, totals, amount due
- Currency shown based on the business's country setting
- Sent as a downloadable PDF file directly in the chat

### Subscription & Access Control
- New users complete onboarding then wait for admin approval
- Admin notified immediately on new signup via Telegram
- `/trial <chat_id> [days]` — start free trial (default 7 days, configurable)
- `/approve <chat_id> [days]` — activate paid subscription
- `/status` — view all tenants with subscription status and days remaining
- Access blocked with friendly message when trial/subscription expires
- 2-day warning injected into conversation before expiry

### Reports
- Weekly profit breakdown: revenue, ingredient cost, packaging cost, gross profit

### Image Processing
- Send a photo of a handwritten recipe → bot creates the recipe with all components
- Send a payment receipt → bot extracts amount and method, helps record the payment
- Send a WhatsApp/SMS order screenshot → bot creates the order

---

## How It Works

### For the user

The user messages the Telegram bot in plain language. The bot understands the intent, performs the action, and replies — all in one turn. For follow-up questions ("which customer did you mean?"), the bot asks and remembers the context.

### Under the hood

```
User message
     │
     ▼
Telegram Listener          ← receives message, resolves tenant
     │
     ▼
Request Handler            ← manages conversation history
     │
     ▼
LLM Agent (GPT-4.1 nano)   ← understands intent, decides which tool to call
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

The LLM agent uses **tool calling** — it receives a list of available operations (create_order, add_inventory, etc.) and decides which one to invoke based on the user's message. This means:
- No hardcoded intent routing
- Natural follow-up handling ("replace" after being asked about a duplicate)
- The model handles ambiguity gracefully

---

## Architecture

### Components

| Component | File | Responsibility |
|---|---|---|
| Telegram Adapter | `app/telegram_listener.py` | Receive messages, download photos, send replies |
| Request Handler | `app/handlers/request_handler.py` | Conversation history, image processing, admin routing |
| Agent | `app/services/agent_service.py` | LLM agent loop with tool calling |
| Tool Executor | `app/services/tool_executor.py` | Maps tool names to service method calls |
| Services | `app/services/*.py` | Business logic, one file per domain |
| Models | `app/models.py` | Database schema (SQLAlchemy ORM) |
| Config | `app/config.py` | Environment-based configuration |

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
| Setup | Zero config | Connection string required |

**SQLite per-tenant layout:**
```
/data/
    tenants.db                    ← shared registry (chat_id → tenant_id)
    <tenant_id>.db                ← Priya's bakery data
    <tenant_id>.db                ← Raj's bakery data
    ...
```

Each request opens the shared `tenants.db` to resolve the tenant, then opens that tenant's own `.db` file for all business operations. SQLite's write lock is per-file, so concurrent requests from different tenants never block each other. Engines are cached in-process and reused across requests.

### Deployment options

**Option A — Shared server (recommended for most cases)**

One EC2 instance serves all tenants. Each tenant gets their own SQLite file.

```
EC2 t4g.small (shared)
├── /data/tenants.db
├── /data/<tenant_id>.db    ← Priya
├── /data/<tenant_id>.db    ← Raj
└── /data/<tenant_id>.db    ← Meena
```

Cost: ~$10/month for the server regardless of tenant count. LLM API adds ~$0.22/tenant/month.

**Option B — Dedicated stack per tenant**

Each tenant gets their own EC2 instance and database. Use for enterprise customers or compliance requirements.

```
EC2 t4g.nano  ←→  SQLite on EBS volume  ←→  S3 backup bucket
     or
EC2 t4g.nano  ←→  RDS db.t4g.micro (PostgreSQL)
```

Cost: ~$4/month (SQLite) or ~$18/month (PostgreSQL) per tenant.

Deployed with AWS CDK (TypeScript):
```bash
cdk deploy --context tenantId=my-business --context dbEngine=sqlite
```

See `DEPLOYMENT.md` for full setup instructions.

### Conversation history

The last 20 message turns are kept in memory per chat and sent as context on every LLM call. This allows the model to understand follow-up messages without re-explaining context. History is in-memory only — it resets on bot restart, which is intentional (it's conversational context, not business data).

---

## Data Model

```
Tenant ──┬── Customer ──── Order ──┬── OrderItem ──── Recipe ──── RecipeComponent
         │                         └── Payment                         │
         ├── InventoryItem ─────────────────────────────────────────────┘
         └── AuditLog
```

- **Tenant**: one per chat_id, isolates all data; stores business name and country
- **Customer**: name + phone, unique per tenant
- **InventoryItem**: ingredient or packaging, with quantity, unit, cost
- **Recipe**: name + yield per batch, linked to inventory items via components
- **Order**: customer + delivery date + status (pending/delivered/cancelled)
- **OrderItem**: recipe name + quantity + selling price per order
- **Payment**: amount + method per order
- **AuditLog**: records all UPDATE/DELETE operations

---

## Deployment

See the Database section above for deployment options and cost breakdown.

---

### Admin Access

Set `ADMIN_CHAT_ID` to your Telegram chat_id. As admin:
- You skip the welcome screen and operate on owner data directly (shown as `Admin — <business name>`)
- `/status` — lists all tenants with subscription status (⏳ pending, 🎁 trial, ✅ active, 🔴 expired)
- `/switch` — lists all tenants with business names
- `/switch <chat_id>` — switches active tenant and clears history
- `/trial <chat_id> [days]` — start/reset trial (default 7 days); owner notified
- `/approve <chat_id> [days]` — activate paid subscription (default 30 days); owner notified
- `/delete <chat_id>` — permanently delete a tenant's data (for deletion requests)

For dedicated single-tenant deployments, set `OWNER_CHAT_ID` to the owner's chat_id.

---

## Data Security

- All data encrypted at rest (EBS AES-256, both root and data volumes)
- All data encrypted in transit (TLS)
- No public ports open on EC2 — SSH via SSM Session Manager only
- Secrets stored in AWS SSM Parameter Store (KMS encrypted)
- OpenAI API: inputs not used for training; retained max 30 days
- Telegram: messages delivered to bot server, not stored long-term
- Privacy policy: `PRIVACY_POLICY.md`
- User data deletion: admin `/delete` command fulfils deletion requests

---

## Technology Stack

| Layer | Technology |
|---|---|
| Language | Python 3.11+ |
| Messaging | python-telegram-bot (polling mode) |
| LLM — Agent/Intent | Amazon Bedrock Nova Lite (tool calling, cheap) |
| LLM — Image processing | OpenAI GPT-4o mini (vision, handwriting) |
| PDF generation | ReportLab (canvas API) |
| ORM | SQLAlchemy 2.0 |
| Migrations | Alembic |
| Database | SQLite per-tenant (shared server) or PostgreSQL (production) |
| Infrastructure | AWS CDK (TypeScript) |
| Compute | EC2 t3.micro (free tier eligible) |
| Backup | S3 (SQLite snapshots via Python sqlite3 backup API) |
| Webhook tunnel | Cloudflare Tunnel (HTTPS, free) |
| Config | pydantic-settings (.env file) |
| Tests | pytest |

---

## Extending to Other Platforms

The bot is platform-agnostic by design. `telegram_listener.py` is a thin adapter — it handles Telegram protocol only. All business logic lives in `RequestHandler`.

Adding WhatsApp support means writing `whatsapp_listener.py` that calls the same `handler.handle_text()` and `handler.handle_image()` methods. Zero business logic changes required.

---

## Project Structure

```
app/
├── handlers/
│   └── request_handler.py      # Agent runner, history, onboarding, admin, image processing
├── services/
│   ├── agent_service.py        # LLM agent loop + tool definitions (Bedrock Nova Lite)
│   ├── tool_executor.py        # Tool name → service method mapping
│   ├── order_finder.py         # Order lookup utilities
│   ├── customer_service.py
│   ├── inventory_service.py
│   ├── order_service.py
│   ├── payment_service.py
│   ├── recipe_service.py
│   ├── reporting_service.py
│   ├── tenant_service.py       # Subscription management, currency mapping
│   ├── image_service.py        # GPT-4o Vision for handwritten images
│   ├── invoice_service.py      # PDF invoice generation (ReportLab)
│   ├── backup_service.py       # Hourly SQLite → S3 backup
│   └── llm_service.py          # OpenAI client for image processing
├── bedrock_client.py           # AWS Bedrock client (Nova Lite)
├── config.py                   # Environment configuration
├── database.py                 # Per-tenant SQLite engine registry
├── models.py                   # ORM models (portable UUID/JSON types)
├── error_handler.py
├── llm_client.py               # HTTP client for OpenAI API
├── telegram_listener.py        # Telegram adapter
├── instagram_listener.py       # Instagram DM webhook + order detection
└── webhook_server.py           # FastAPI server for Instagram webhooks (port 8000)

infra/
├── bin/app.ts                  # CDK entry point (deploymentId context)
└── lib/kitchenos-stack.ts      # KitchenOsStack: EC2 t3.micro + EBS/RDS + S3

migrations/                     # Alembic schema migrations
scripts/
├── ec2_setup.sh                # Full EC2 setup script
├── migrate_pg_to_sqlite.py     # Postgres → SQLite migration
├── migrate_single_to_per_tenant.py  # Single-file → per-tenant migration
└── update_webhook_url.py       # Auto-update Meta webhook on tunnel URL change
tests/                          # pytest test suite
```

---

## Future Work

| Feature | Description |
|---|---|
| **GST export sheet** | Generate GST-ready CSV/Excel with GSTIN, HSN codes, tax breakdowns for Indian businesses |
| **Schedule C export** | US tax-ready CSV mapping revenue and expenses to Schedule C categories (sole proprietor filing) |
| **Monthly P&L PDF** | Auto-generated profit & loss statement as a downloadable PDF, shareable with accountants |
| **Multi-user access** | Allow multiple staff members to use the same bot with role-based permissions (owner vs. staff) |
| **Auto WhatsApp sync** | Receive orders directly from WhatsApp messages without manual entry |
| **Instagram order detection** | Monitor Instagram DMs, detect confirmed orders, notify owner for confirmation. Infrastructure built — blocked on Meta app review process. |
| **Stripe / Razorpay integration** | Accept online payments, auto-reconcile with order records, send payment links to customers |
