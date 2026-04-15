#!/usr/bin/env python3
"""
Verification script for database models.
This script verifies that all models are correctly defined and can be imported.
"""

from app.models import (
    Tenant, Customer, InventoryItem, Recipe, RecipeComponent,
    Order, OrderItem, Payment, AuditLog, Base
)
from sqlalchemy import inspect

def verify_models():
    """Verify all database models are correctly defined."""
    
    print("=" * 70)
    print("DATABASE MODELS VERIFICATION")
    print("=" * 70)
    
    # List of all models with their expected attributes
    models_spec = {
        'Tenant': {
            'table': 'tenants',
            'pk': 'tenant_id',
            'unique_constraints': ['chat_id'],
            'foreign_keys': []
        },
        'Customer': {
            'table': 'customers',
            'pk': 'customer_id',
            'unique_constraints': [],
            'foreign_keys': ['tenant_id']
        },
        'InventoryItem': {
            'table': 'inventory_items',
            'pk': 'item_id',
            'unique_constraints': [],
            'foreign_keys': ['tenant_id']
        },
        'Recipe': {
            'table': 'recipes',
            'pk': 'recipe_id',
            'unique_constraints': [],
            'foreign_keys': ['tenant_id']
        },
        'RecipeComponent': {
            'table': 'recipe_components',
            'pk': 'component_id',
            'unique_constraints': [],
            'foreign_keys': ['recipe_id', 'item_id']
        },
        'Order': {
            'table': 'orders',
            'pk': 'order_id',
            'unique_constraints': [],
            'foreign_keys': ['tenant_id', 'customer_id']
        },
        'OrderItem': {
            'table': 'order_items',
            'pk': 'order_item_id',
            'unique_constraints': [],
            'foreign_keys': ['order_id', 'recipe_id']
        },
        'Payment': {
            'table': 'payments',
            'pk': 'payment_id',
            'unique_constraints': [],
            'foreign_keys': ['tenant_id', 'order_id']
        },
        'AuditLog': {
            'table': 'audit_logs',
            'pk': 'log_id',
            'unique_constraints': [],
            'foreign_keys': ['tenant_id']
        }
    }
    
    models = {
        'Tenant': Tenant,
        'Customer': Customer,
        'InventoryItem': InventoryItem,
        'Recipe': Recipe,
        'RecipeComponent': RecipeComponent,
        'Order': Order,
        'OrderItem': OrderItem,
        'Payment': Payment,
        'AuditLog': AuditLog
    }
    
    all_passed = True
    
    for model_name, model_class in models.items():
        print(f"\n{model_name}:")
        spec = models_spec[model_name]
        
        # Check table name
        if model_class.__tablename__ == spec['table']:
            print(f"  ✓ Table name: {spec['table']}")
        else:
            print(f"  ✗ Table name mismatch: expected {spec['table']}, got {model_class.__tablename__}")
            all_passed = False
        
        # Check primary key
        pk_cols = [col.name for col in model_class.__table__.primary_key.columns]
        if spec['pk'] in pk_cols:
            print(f"  ✓ Primary key: {spec['pk']}")
        else:
            print(f"  ✗ Primary key mismatch: expected {spec['pk']}, got {pk_cols}")
            all_passed = False
        
        # Check foreign keys
        fk_cols = [col.name for col in model_class.__table__.columns if col.foreign_keys]
        if set(fk_cols) == set(spec['foreign_keys']):
            if fk_cols:
                print(f"  ✓ Foreign keys: {', '.join(fk_cols)}")
        else:
            print(f"  ✗ Foreign key mismatch: expected {spec['foreign_keys']}, got {fk_cols}")
            all_passed = False
        
        # Check for composite unique constraints
        composite_unique = []
        for constraint in model_class.__table__.constraints:
            if hasattr(constraint, 'columns') and len(constraint.columns) > 1:
                cols = tuple(sorted([c.name for c in constraint.columns]))
                composite_unique.append(cols)
        
        if model_name == 'Customer':
            if ('phone', 'tenant_id') in composite_unique or ('tenant_id', 'phone') in composite_unique:
                print(f"  ✓ Unique constraint: (tenant_id, phone)")
            else:
                print(f"  ✗ Missing unique constraint: (tenant_id, phone)")
                all_passed = False
        
        if model_name == 'InventoryItem':
            if ('name', 'tenant_id') in composite_unique or ('tenant_id', 'name') in composite_unique:
                print(f"  ✓ Unique constraint: (tenant_id, name)")
            else:
                print(f"  ✗ Missing unique constraint: (tenant_id, name)")
                all_passed = False
        
        if model_name == 'Recipe':
            if ('name', 'tenant_id') in composite_unique or ('tenant_id', 'name') in composite_unique:
                print(f"  ✓ Unique constraint: (tenant_id, name)")
            else:
                print(f"  ✗ Missing unique constraint: (tenant_id, name)")
                all_passed = False
    
    print("\n" + "=" * 70)
    if all_passed:
        print("✓ ALL MODELS VERIFIED SUCCESSFULLY!")
        print("✓ Task 2.1 Complete: All 9 models created with required constraints")
    else:
        print("✗ SOME VERIFICATIONS FAILED")
    print("=" * 70)
    
    return all_passed

if __name__ == "__main__":
    success = verify_models()
    exit(0 if success else 1)
