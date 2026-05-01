# Database Migrations

This directory contains Alembic database migrations for the Bakery Operations Telegram Bot.

## Overview

Alembic is used to manage database schema changes. All migrations are stored in the `versions/` directory.

## Prerequisites

- PostgreSQL database running and accessible
- Database connection configured in `.env` file
- Alembic installed (included in requirements.txt)

## Common Commands

### Apply all pending migrations
```bash
alembic upgrade head
```

### Rollback one migration
```bash
alembic downgrade -1
```

### Rollback to a specific revision
```bash
alembic downgrade <revision_id>
```

### View current migration status
```bash
alembic current
```

### View migration history
```bash
alembic history
```

### Create a new migration (auto-generate from model changes)
```bash
alembic revision --autogenerate -m "Description of changes"
```

### Create a new empty migration
```bash
alembic revision -m "Description of changes"
```

## Initial Migration

The initial migration (`07c9fd7f518a`) creates all tables with the following features:

### Tables Created
1. **tenants** - Bakery organizations with unique chat_id
2. **customers** - Customer records with unique (tenant_id, phone) constraint
3. **inventory_items** - Ingredients and packaging with unique (tenant_id, name) constraint
4. **recipes** - Product recipes with unique (tenant_id, name) constraint
5. **recipe_components** - Links recipes to inventory items
6. **orders** - Customer orders with delivery dates
7. **order_items** - Individual items in orders
8. **payments** - Payment records for orders
9. **audit_logs** - Audit trail for data modifications

### Indexes Created
- All `tenant_id` columns are indexed for query performance
- All foreign key columns are indexed
- `chat_id` in tenants table has unique index

### Constraints
- Primary keys on all tables (UUID type)
- Foreign key constraints for referential integrity
- Unique constraints for:
  - tenants.chat_id
  - customers.(tenant_id, phone)
  - inventory_items.(tenant_id, name)
  - recipes.(tenant_id, name)

## Database Connection

The database URL is configured in `app/config.py` and loaded from the `.env` file:

```
DATABASE_URL=postgresql://user:password@localhost:5432/bakery_ops
```

## Troubleshooting

### Connection refused error
Ensure PostgreSQL is running:
```bash
# macOS with Homebrew
brew services start postgresql

# Linux with systemd
sudo systemctl start postgresql
```

### Migration conflicts
If you have migration conflicts, you can:
1. Rollback to a common ancestor: `alembic downgrade <revision>`
2. Resolve conflicts manually
3. Apply migrations: `alembic upgrade head`

### Reset database (CAUTION: destroys all data)
```bash
alembic downgrade base
alembic upgrade head
```

## Best Practices

1. **Always review auto-generated migrations** - Alembic's autogenerate is helpful but not perfect
2. **Test migrations on a copy of production data** before applying to production
3. **Never edit applied migrations** - Create a new migration to fix issues
4. **Keep migrations small and focused** - One logical change per migration
5. **Add comments** to complex migrations explaining the reasoning
