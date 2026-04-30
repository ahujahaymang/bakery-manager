"""
Inventory Service for managing bakery inventory operations.

This service handles inventory item creation, updates, retrieval, and listing
with proper tenant isolation and validation.
"""

from typing import List, Optional, Dict, Any
from uuid import UUID
from decimal import Decimal
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from sqlalchemy import func

from app.models import InventoryItem
from app.services.audit_service import AuditService


# Valid categories and units
VALID_CATEGORIES = {"ingredient", "packaging"}
VALID_UNITS = {"kg", "g", "litre", "ml", "pcs"}


class InventoryService:
    """
    Service for managing inventory operations.
    
    Handles inventory item creation, updates, retrieval, and listing with
    tenant isolation and validation.
    """
    
    def __init__(self, db: Session):
        """
        Initialize InventoryService with database session.
        
        Args:
            db: SQLAlchemy database session
        """
        self.db = db
        self.audit_service = AuditService(db)
    def __init__(self, db: Session):
        """
        Initialize InventoryService with database session.
        
        Args:
            db: SQLAlchemy database session
        """
        self.db = db
    
    def create_item(
        self,
        tenant_id: UUID,
        name: str,
        category: str,
        quantity: Decimal,
        unit: str,
        cost_per_unit: Decimal
    ) -> InventoryItem:
        """
        Create a new inventory item with validation.
        
        Validates category, unit, quantity, and cost_per_unit before creating
        the inventory item record.
        
        Args:
            tenant_id: UUID of the tenant
            name: Item name
            category: Item category ("ingredient" or "packaging")
            quantity: Item quantity
            unit: Unit of measurement (kg, g, litre, ml, pcs)
            cost_per_unit: Cost per unit
        
        Returns:
            InventoryItem: The newly created inventory item
        
        Raises:
            ValueError: If validation fails
        
        Requirements:
            - 5.1: Extract item details from message
            - 5.2: Validate category is "ingredient" or "packaging"
            - 5.3: Validate unit is one of: kg, g, litre, ml, pcs
            - 5.4: Request missing information if fields are missing
            - 5.5: Create Inventory_Item record with all required fields
            - 5.6: Confirm item was added
        """
        # Validate required fields
        if not name or not name.strip():
            raise ValueError("Item name is required")
        
        if not category or not category.strip():
            raise ValueError("Item category is required")
        
        if unit is None or (isinstance(unit, str) and not unit.strip()):
            raise ValueError("Item unit is required")
        
        if quantity is None:
            raise ValueError("Item quantity is required")
        
        if cost_per_unit is None:
            raise ValueError("Item cost per unit is required")
        
        # Clean inputs
        name = name.strip()
        category = category.strip().lower()
        unit = unit.strip().lower()
        
        # Validate category
        if category not in VALID_CATEGORIES:
            raise ValueError(
                f"Invalid category '{category}'. Must be one of: {', '.join(sorted(VALID_CATEGORIES))}"
            )
        
        # Validate unit
        if unit not in VALID_UNITS:
            raise ValueError(
                f"Invalid unit '{unit}'. Must be one of: {', '.join(sorted(VALID_UNITS))}"
            )
        
        # Validate quantity is positive
        try:
            quantity = Decimal(str(quantity))
            if quantity <= 0:
                raise ValueError("Item quantity must be positive")
        except (ValueError, TypeError) as e:
            if "positive" in str(e):
                raise
            raise ValueError(f"Invalid quantity value: {quantity}")
        
        # Validate cost_per_unit is positive
        try:
            cost_per_unit = Decimal(str(cost_per_unit))
            if cost_per_unit <= 0:
                raise ValueError("Item cost per unit must be positive")
        except (ValueError, TypeError) as e:
            if "positive" in str(e):
                raise
            raise ValueError(f"Invalid cost per unit value: {cost_per_unit}")
        
        # Check for duplicate name (case-insensitive)
        existing_item = self.db.query(InventoryItem).filter(
            InventoryItem.tenant_id == tenant_id,
            func.lower(InventoryItem.name) == name.lower()
        ).first()
        
        if existing_item:
            raise ValueError(
                f"An inventory item with name '{existing_item.name}' already exists"
            )
        
        # Create inventory item
        try:
            item = InventoryItem(
                tenant_id=tenant_id,
                name=name,
                category=category,
                quantity=quantity,
                unit=unit,
                cost_per_unit=cost_per_unit
            )
            self.db.add(item)
            self.db.commit()
            self.db.refresh(item)
            return item
        except IntegrityError as e:
            self.db.rollback()
            # Handle race condition
            existing = self.db.query(InventoryItem).filter(
                InventoryItem.tenant_id == tenant_id,
                func.lower(InventoryItem.name) == name.lower()
            ).first()
            if existing:
                raise ValueError(
                    f"An inventory item with name '{existing.name}' already exists"
                )
            raise
    
    def update_item(
        self,
        tenant_id: UUID,
        name: str,
        updates: Dict[str, Any]
    ) -> InventoryItem:
        """
        Update an existing inventory item.
        
        Updates specified fields (quantity, cost_per_unit) and logs changes
        to audit log.
        
        Args:
            tenant_id: UUID of the tenant
            name: Item name
            updates: Dictionary of fields to update
        
        Returns:
            InventoryItem: The updated inventory item
        
        Raises:
            ValueError: If item not found or validation fails
        
        Requirements:
            - 6.1: Extract item name and fields to update
            - 6.2: Retrieve Inventory_Item by name and Tenant_ID
            - 6.3: Return error if item does not exist
            - 6.4: Update specified fields and set updated_at
            - 6.5: Log update in audit_logs
            - 6.6: Confirm changes
        """
        if not name or not name.strip():
            raise ValueError("Item name is required")
        
        name = name.strip()
        
        # Retrieve existing item (case-insensitive)
        item = self.db.query(InventoryItem).filter(
            InventoryItem.tenant_id == tenant_id,
            func.lower(InventoryItem.name) == name.lower()
        ).first()
        
        if not item:
            raise ValueError(f"Inventory item '{name}' not found")
        
        # Store old values for audit logging
        old_values = {
            "quantity": str(item.quantity),
            "cost_per_unit": str(item.cost_per_unit)
        }
        
        # Update fields
        if "quantity" in updates:
            try:
                quantity = Decimal(str(updates["quantity"]))
                if quantity <= 0:
                    raise ValueError("Item quantity must be positive")
                item.quantity = quantity
            except (ValueError, TypeError) as e:
                if "positive" in str(e):
                    raise
                raise ValueError(f"Invalid quantity value: {updates['quantity']}")
        
        if "cost_per_unit" in updates:
            try:
                cost_per_unit = Decimal(str(updates["cost_per_unit"]))
                if cost_per_unit <= 0:
                    raise ValueError("Item cost per unit must be positive")
                item.cost_per_unit = cost_per_unit
            except (ValueError, TypeError) as e:
                if "positive" in str(e):
                    raise
                raise ValueError(f"Invalid cost per unit value: {updates['cost_per_unit']}")
        
        # Commit changes
        self.db.commit()
        self.db.refresh(item)
        
        # Store new values for audit logging
        new_values = {
            "quantity": str(item.quantity),
            "cost_per_unit": str(item.cost_per_unit)
        }
        
        # Log to audit_logs
        self.audit_service.log_change(
            tenant_id=tenant_id,
            table_name="inventory_items",
            record_id=item.item_id,
            operation_type="UPDATE",
            old_values=old_values,
            new_values=new_values
        )
        
        return item
    
    def get_item(
        self,
        tenant_id: UUID,
        name: str
    ) -> Optional[InventoryItem]:
        """
        Retrieve an inventory item by name.
        
        Args:
            tenant_id: UUID of the tenant
            name: Item name
        
        Returns:
            Optional[InventoryItem]: The item if found, None otherwise
        
        Requirements:
            - 7.1: Extract item name
            - 7.2: Retrieve Inventory_Item by name and Tenant_ID
            - 7.3: Display item details if exists
            - 7.4: Return message if item not found
        """
        if not name or not name.strip():
            return None
        
        return self.db.query(InventoryItem).filter(
            InventoryItem.tenant_id == tenant_id,
            func.lower(InventoryItem.name) == name.strip().lower()
        ).first()
    
    def list_items(
        self,
        tenant_id: UUID
    ) -> Dict[str, List[InventoryItem]]:
        """
        List all inventory items for a tenant, grouped by category.
        
        Args:
            tenant_id: UUID of the tenant
        
        Returns:
            Dict[str, List[InventoryItem]]: Items grouped by category
        
        Requirements:
            - 8.1: Retrieve all Inventory_Items for Tenant_ID
            - 8.2: Group items by category
            - 8.3: Sort items within each category
        """
        items = self.db.query(InventoryItem).filter(
            InventoryItem.tenant_id == tenant_id
        ).order_by(InventoryItem.category, InventoryItem.name).all()
        
        # Group by category
        grouped = {
            "ingredient": [],
            "packaging": []
        }
        
        for item in items:
            if item.category in grouped:
                grouped[item.category].append(item)
        
        return grouped
    
    def get_item_by_id(
        self,
        tenant_id: UUID,
        item_id: UUID
    ) -> Optional[InventoryItem]:
        """
        Retrieve an inventory item by ID with tenant isolation.
        
        Args:
            tenant_id: UUID of the tenant
            item_id: UUID of the item
        
        Returns:
            Optional[InventoryItem]: The item if found, None otherwise
        """
        return self.db.query(InventoryItem).filter(
            InventoryItem.tenant_id == tenant_id,
            InventoryItem.item_id == item_id
        ).first()
