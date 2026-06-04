"""
SQLite migration script — run on the server to apply schema changes
that Base.metadata.create_all doesn't handle (new columns on existing tables,
new tables added after initial deployment).

Usage:
    python3.11 /opt/kitchenos/scripts/migrate_sqlite.py
"""

import sqlite3
import glob

DBS = [d for d in glob.glob("/data/*.db") if not d.endswith(("-wal", "-shm"))]


def add_col(conn, table, col, typedef):
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if col not in cols:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typedef}")
            return True
        except Exception as e:
            print(f"  Skip {table}.{col}: {e}")
    return False


def create_table_if_missing(conn, name, ddl):
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()]
    if name not in tables:
        conn.execute(ddl)
        return True
    return False


for db in DBS:
    print(f"\n{db}")
    conn = sqlite3.connect(db)

    # ── tenants ────────────────────────────────────────────────────────────
    if add_col(conn, "tenants", "order_template", "TEXT"):
        print("  + tenants.order_template")
    if add_col(conn, "tenants", "razorpay_key_id", "TEXT"):
        print("  + tenants.razorpay_key_id")
    if add_col(conn, "tenants", "razorpay_key_secret", "TEXT"):
        print("  + tenants.razorpay_key_secret")

    # ── orders ─────────────────────────────────────────────────────────────
    if add_col(conn, "orders", "booth_session_id", "TEXT"):
        print("  + orders.booth_session_id")

    # ── payments ───────────────────────────────────────────────────────────
    if add_col(conn, "payments", "razorpay_payment_id", "TEXT"):
        print("  + payments.razorpay_payment_id")
    if add_col(conn, "payments", "status", "TEXT NOT NULL DEFAULT 'completed'"):
        print("  + payments.status")

    # ── order_items ────────────────────────────────────────────────────────
    if add_col(conn, "order_items", "customization_charge", "NUMERIC(10,2) NOT NULL DEFAULT 0"):
        print("  + order_items.customization_charge")
    if add_col(conn, "order_items", "customization_note", "TEXT"):
        print("  + order_items.customization_note")

    # ── products / product_variants ────────────────────────────────────────
    if create_table_if_missing(conn, "products", """
        CREATE TABLE products (
            product_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            category TEXT,
            image_url TEXT,
            recipe_id TEXT,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            FOREIGN KEY(tenant_id) REFERENCES tenants(tenant_id),
            UNIQUE(tenant_id, name)
        )
    """):
        print("  + table: products")

    if create_table_if_missing(conn, "product_variants", """
        CREATE TABLE product_variants (
            variant_id TEXT PRIMARY KEY,
            product_id TEXT NOT NULL,
            size_label TEXT NOT NULL,
            price NUMERIC(10,2) NOT NULL,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            FOREIGN KEY(product_id) REFERENCES products(product_id),
            UNIQUE(product_id, size_label)
        )
    """):
        print("  + table: product_variants")

    # ── booth tables ───────────────────────────────────────────────────────
    if create_table_if_missing(conn, "booth_sessions", """
        CREATE TABLE booth_sessions (
            session_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            name TEXT NOT NULL,
            started_at DATETIME NOT NULL,
            ended_at DATETIME,
            created_at DATETIME NOT NULL,
            FOREIGN KEY(tenant_id) REFERENCES tenants(tenant_id)
        )
    """):
        print("  + table: booth_sessions")

    if create_table_if_missing(conn, "booth_session_items", """
        CREATE TABLE booth_session_items (
            item_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            variant_id TEXT NOT NULL,
            booth_price NUMERIC(10,2) NOT NULL,
            stock_qty INTEGER,
            sold_qty INTEGER NOT NULL DEFAULT 0,
            created_at DATETIME NOT NULL,
            FOREIGN KEY(session_id) REFERENCES booth_sessions(session_id),
            FOREIGN KEY(variant_id) REFERENCES product_variants(variant_id)
        )
    """):
        print("  + table: booth_session_items")

    # ── conversation_messages ──────────────────────────────────────────────
    if create_table_if_missing(conn, "conversation_messages", """
        CREATE TABLE conversation_messages (
            message_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            chat_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at DATETIME NOT NULL,
            FOREIGN KEY(tenant_id) REFERENCES tenants(tenant_id)
        )
    """):
        print("  + table: conversation_messages")

    if create_table_if_missing(conn, "purchase_expenses", """
        CREATE TABLE purchase_expenses (
            expense_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            amount NUMERIC(10,2) NOT NULL,
            vendor_name TEXT,
            expense_date DATE NOT NULL,
            category TEXT NOT NULL DEFAULT 'other',
            is_capital TEXT NOT NULL DEFAULT 'false',
            description TEXT,
            notes TEXT,
            created_at DATETIME NOT NULL,
            FOREIGN KEY(tenant_id) REFERENCES tenants(tenant_id)
        )
    """):
        print("  + table: purchase_expenses")
    else:
        # Table exists — add new columns if missing
        cols = [r[1] for r in conn.execute("PRAGMA table_info(purchase_expenses)").fetchall()]
        if "category" not in cols:
            conn.execute("ALTER TABLE purchase_expenses ADD COLUMN category TEXT NOT NULL DEFAULT 'other'")
            print("  + purchase_expenses.category")
        if "is_capital" not in cols:
            conn.execute("ALTER TABLE purchase_expenses ADD COLUMN is_capital TEXT NOT NULL DEFAULT 'false'")
            print("  + purchase_expenses.is_capital")
        if "description" not in cols:
            conn.execute("ALTER TABLE purchase_expenses ADD COLUMN description TEXT")
            print("  + purchase_expenses.description")

    conn.commit()
    conn.close()

print("\nMigration complete.")
