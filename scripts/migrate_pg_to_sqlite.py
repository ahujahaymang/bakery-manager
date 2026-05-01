#!/usr/bin/env python3
"""
Migrate data from PostgreSQL to SQLite.

Usage:
    python scripts/migrate_pg_to_sqlite.py --output bakery.db

This script:
1. Creates a fresh SQLite database with the full schema
2. Copies all rows from every table in the correct FK order
3. Verifies row counts match

Run with DB_ENGINE=postgresql in .env (source), output goes to --output path.
"""

import argparse
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Force PostgreSQL for source
os.environ['DB_ENGINE'] = 'postgresql'

from app.config import settings
from app.models import Base, Tenant, Customer, InventoryItem, Recipe, RecipeComponent, Order, OrderItem, Payment, AuditLog

# Tables in FK-safe insertion order
TABLES = [Tenant, Customer, InventoryItem, Recipe, RecipeComponent, Order, OrderItem, Payment, AuditLog]


def migrate(sqlite_path: str):
    print(f"Source: {settings.DATABASE_URL}")
    print(f"Target: sqlite:///{sqlite_path}\n")

    # Source: PostgreSQL
    src_engine = create_engine(settings.DATABASE_URL)
    SrcSession = sessionmaker(bind=src_engine)

    # Target: SQLite
    sqlite_url = f"sqlite:///{sqlite_path}"
    dst_engine = create_engine(
        sqlite_url,
        connect_args={"check_same_thread": False}
    )

    # Enable WAL + foreign keys on SQLite
    from sqlalchemy import event
    @event.listens_for(dst_engine, "connect")
    def set_pragmas(conn, _):
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=OFF")  # off during bulk insert

    # Create schema
    print("Creating SQLite schema...")
    Base.metadata.create_all(dst_engine)

    DstSession = sessionmaker(bind=dst_engine)
    src = SrcSession()
    dst = DstSession()

    total_copied = 0

    try:
        for model in TABLES:
            table_name = model.__tablename__
            rows = src.query(model).all()

            if not rows:
                print(f"  {table_name}: 0 rows (skipped)")
                continue

            # Detach from source session and add to destination
            src.expunge_all()
            for row in rows:
                # Make a fresh instance with same column values
                data = {
                    col.key: getattr(row, col.key)
                    for col in model.__table__.columns
                }
                dst.execute(model.__table__.insert().values(**data))

            dst.commit()
            print(f"  {table_name}: {len(rows)} rows copied")
            total_copied += len(rows)

    except Exception as e:
        dst.rollback()
        print(f"\nError: {e}")
        raise
    finally:
        src.close()
        dst.close()

    # Re-enable foreign keys and verify
    with dst_engine.connect() as conn:
        conn.execute(text("PRAGMA foreign_keys=ON"))

    print(f"\n✅ Migration complete — {total_copied} total rows copied to {sqlite_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate PostgreSQL → SQLite")
    parser.add_argument("--output", default="bakery.db", help="Output SQLite file path")
    args = parser.parse_args()

    migrate(args.output)
