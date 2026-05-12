"""
Order Service for managing bakery order operations.

This service handles order creation, delivery marking, and order retrieval
with proper tenant isolation, customer resolution, and validation.
"""

from typing import List, Optional, Dict, Any
from uuid import UUID
from decimal import Decimal
from datetime import date, datetime
from dataclasses import dataclass
from sqlalchemy.orm import Session
from sqlalchemy import and_, func

from app.models import Order, OrderItem, Customer, Recipe, Payment
from app.services.customer_service import CustomerService
from app.services.audit_service import AuditService


@dataclass
class OrderItemCreate:
    """Data class for creating an order item."""
    recipe_name: str
    quantity: int
    selling_price: Decimal
    customization_charge: Decimal = Decimal("0")
    customization_note: str = None


@dataclass
class OrderCreate:
    """Data class for creating an order."""
    customer_identifier: str  # name or phone
    delivery_date: date
    items: List[OrderItemCreate]
    delivery_address: str = None  # overrides customer's default address if provided


class OrderService:
    """
    Service for managing order operations.
    
    Handles order creation with customer resolution, delivery marking,
    and order retrieval with tenant isolation and validation.
    """
    
    def __init__(self, db: Session):
        """
        Initialize OrderService with database session.
        
        Args:
            db: SQLAlchemy database session
        """
        self.db = db
        self.customer_service = CustomerService(db)
        self.audit_service = AuditService(db)
    
    def create_order(
        self,
        tenant_id: UUID,
        order: OrderCreate
    ) -> Order:
        """
        Create a new order with customer resolution and validation.
        
        Resolves customer by name or phone, validates delivery date is not
        in the past, creates Order record with status "pending", and creates
        OrderItem records for each item with selling_price validation.
        
        Args:
            tenant_id: UUID of the tenant
            order: OrderCreate data with customer identifier, delivery date, and items
        
        Returns:
            Order: The newly created order with order items
        
        Raises:
            ValueError: If validation fails or customer resolution fails
        
        Requirements:
            - 12.1: Extract customer identifier, delivery date, and order items
            - 12.2: Resolve Customer by name or phone for Tenant_ID
            - 12.3: Request disambiguation if multiple customers match
            - 12.4: Create Order record with status "pending"
            - 12.5: Create Order_Item records for each item
            - 12.6: Confirm order with details
        """
        # Validate required fields
        if not order.customer_identifier or not order.customer_identifier.strip():
            raise ValueError("Customer identifier is required")
        
        if order.delivery_date is None:
            raise ValueError("Delivery date is required")
        
        if not order.items or len(order.items) == 0:
            raise ValueError("At least one order item is required")
        
        # Validate delivery_date is not in the past
        today = date.today()
        if order.delivery_date < today:
            raise ValueError(
                f"Delivery date cannot be in the past. You provided: {order.delivery_date}"
            )
        
        # Resolve customer by name or phone
        customer_identifier = order.customer_identifier.strip()
        customers = self.customer_service.get_customer(tenant_id, customer_identifier)
        
        if len(customers) == 0:
            raise ValueError(
                f"No customer found matching '{customer_identifier}'. "
                "Please check the name or phone number."
            )
        
        if len(customers) > 1:
            # Multiple customers match - need disambiguation
            customer_list = []
            for c in customers:
                customer_list.append(f"{c.name} ({c.phone})")
            
            raise ValueError(
                f"Multiple customers match '{customer_identifier}'. "
                f"Please specify which customer by phone number: {', '.join(customer_list)}"
            )
        
        # Single customer match
        customer = customers[0]
        
        # Validate and resolve recipes for all order items
        validated_items = []
        missing_recipes = []
        
        for item in order.items:
            if not item.recipe_name or not item.recipe_name.strip():
                raise ValueError("Recipe name is required for all order items")
            
            if item.quantity is None:
                raise ValueError(f"Quantity is required for recipe '{item.recipe_name}'")
            
            if item.selling_price is None:
                raise ValueError(f"Selling price is required for recipe '{item.recipe_name}'")
            
            # Validate quantity is positive integer
            try:
                quantity = int(item.quantity)
                if quantity <= 0:
                    raise ValueError(
                        f"Quantity must be a positive integer for recipe '{item.recipe_name}'. "
                        f"You provided: {item.quantity}"
                    )
            except (ValueError, TypeError) as e:
                if "positive" in str(e):
                    raise
                raise ValueError(
                    f"Invalid quantity value for recipe '{item.recipe_name}': {item.quantity}"
                )
            
            # Validate selling_price is positive
            try:
                selling_price = Decimal(str(item.selling_price))
                if selling_price <= 0:
                    raise ValueError(
                        f"Selling price must be positive for recipe '{item.recipe_name}'. "
                        f"You provided: {item.selling_price}"
                    )
            except (ValueError, TypeError) as e:
                if "positive" in str(e):
                    raise
                raise ValueError(
                    f"Invalid selling price value for recipe '{item.recipe_name}': {item.selling_price}"
                )
            
            # Try to retrieve recipe (case-insensitive)
            recipe = self.db.query(Recipe).filter(
                Recipe.tenant_id == tenant_id,
                func.lower(Recipe.name) == item.recipe_name.strip().lower()
            ).first()
            
            if not recipe:
                # Track missing recipes but don't fail
                missing_recipes.append(item.recipe_name)
            
            validated_items.append({
                'recipe': recipe,  # Can be None
                'recipe_name': item.recipe_name.strip(),
                'quantity': quantity,
                'selling_price': selling_price,
                'customization_charge': Decimal(str(item.customization_charge or 0)),
                'customization_note': item.customization_note,
            })
        
        # Create order — use provided address or fall back to customer's default
        delivery_address = order.delivery_address or customer.address

        new_order = Order(
            tenant_id=tenant_id,
            customer_id=customer.customer_id,
            delivery_date=order.delivery_date,
            delivery_address=delivery_address,
            status="pending"
        )
        self.db.add(new_order)
        self.db.flush()  # Flush to get order_id for order items
        
        # Create order items
        for item_data in validated_items:
            order_item = OrderItem(
                order_id=new_order.order_id,
                recipe_id=item_data['recipe'].recipe_id if item_data['recipe'] else None,
                recipe_name=item_data['recipe_name'],
                quantity=item_data['quantity'],
                selling_price=item_data['selling_price'],
                customization_charge=item_data['customization_charge'],
                customization_note=item_data['customization_note'],
            )
            self.db.add(order_item)
        
        # Commit transaction
        self.db.commit()
        self.db.refresh(new_order)
        
        # Store missing recipes info on the order object for the handler to access
        new_order._missing_recipes = missing_recipes
        
        return new_order
    
    def mark_delivered(
        self,
        tenant_id: UUID,
        order_id: UUID
    ) -> Order:
        """
        Mark an order as delivered with audit logging.
        
        Retrieves order by order_id and tenant_id, updates status to "delivered",
        logs the status change in audit logs, and returns the updated order.
        
        Args:
            tenant_id: UUID of the tenant
            order_id: UUID of the order to mark as delivered
        
        Returns:
            Order: The updated order with status "delivered"
        
        Raises:
            ValueError: If order is not found or validation fails
        
        Requirements:
            - 13.1: Extract order identifier
            - 13.2: Retrieve Order by identifier and Tenant_ID
            - 13.3: Update status to "delivered" and set updated_at
            - 13.4: Log status change in audit_logs
            - 13.5: Confirm order was marked delivered
        """
        # Retrieve order by order_id and tenant_id (tenant isolation)
        order = self.db.query(Order).filter(
            Order.order_id == order_id,
            Order.tenant_id == tenant_id
        ).first()
        
        if not order:
            raise ValueError(
                f"Order not found. Please check the order ID."
            )
        
        # Store old status for audit logging
        old_status = order.status
        
        # Update status to "delivered"
        order.status = "delivered"
        order.updated_at = datetime.utcnow()
        
        # Commit transaction
        self.db.commit()
        self.db.refresh(order)
        
        # Log status change in audit_logs
        self.audit_service.log_change(
            tenant_id=tenant_id,
            table_name="orders",
            record_id=order.order_id,
            operation_type="UPDATE",
            old_values={"status": old_status},
            new_values={"status": "delivered"}
        )
        
        return order
    
    def get_upcoming_orders(
        self,
        tenant_id: UUID
    ) -> List[Dict[str, Any]]:
        """
        Retrieve all upcoming orders with status "pending".
        
        Retrieves all orders with status "pending" for the tenant, sorted by
        delivery_date ascending. Includes customer name, items, quantities,
        delivery date, and total price.
        
        Args:
            tenant_id: UUID of the tenant
        
        Returns:
            List[Dict]: List of upcoming orders with details
        
        Requirements:
            - 14.1: Retrieve all Orders with status "pending" for Tenant_ID
            - 14.2: Sort Orders by delivery_date ascending
            - 14.3: Display order details including customer name, items, quantities, delivery date, and total price
        """
        # Retrieve all pending orders for tenant, sorted by delivery_date
        orders = self.db.query(Order).filter(
            Order.tenant_id == tenant_id,
            Order.status == "pending"
        ).order_by(Order.delivery_date.asc()).all()
        
        result = []
        for order in orders:
            # Get customer details
            customer = self.db.query(Customer).filter(
                Customer.customer_id == order.customer_id
            ).first()
            
            # Get order items with recipe details (LEFT JOIN to include items without recipes)
            order_items = self.db.query(OrderItem, Recipe).outerjoin(
                Recipe, OrderItem.recipe_id == Recipe.recipe_id
            ).filter(
                OrderItem.order_id == order.order_id
            ).all()
            
            # Calculate total price (selling_price + customization_charge per item)
            total_price = Decimal('0')
            items_list = []
            for order_item, recipe in order_items:
                customization = getattr(order_item, 'customization_charge', Decimal('0')) or Decimal('0')
                effective_price = order_item.selling_price + customization
                item_total = order_item.quantity * effective_price
                total_price += item_total
                recipe_name = getattr(order_item, 'recipe_name', None) or (recipe.name if recipe else "Unknown Item")
                items_list.append({
                    'recipe_name': recipe_name,
                    'quantity': order_item.quantity,
                    'selling_price': effective_price,  # combined price for display
                    'item_total': item_total,
                })
            
            result.append({
                'order_id': order.order_id,
                'customer_name': customer.name if customer else "Unknown",
                'customer_phone': customer.phone if customer else "Unknown",
                'delivery_date': order.delivery_date,
                'delivery_address': order.delivery_address,
                'items': items_list,
                'total_price': total_price,
                'created_at': order.created_at
            })
        
        return result
    
    def get_unpaid_orders(
        self,
        tenant_id: UUID
    ) -> List[Dict[str, Any]]:
        """
        Retrieve all orders with no payment or partial payment.
        
        LEFT JOINs orders with payments to identify orders with no associated
        payment or partial payment. Includes customer name, total amount, and
        amount paid.
        
        Args:
            tenant_id: UUID of the tenant
        
        Returns:
            List[Dict]: List of unpaid orders with payment details
        
        Requirements:
            - 16.1: Retrieve all Orders for Tenant_ID
            - 16.2: LEFT JOIN with Payments to identify Orders with no payment or partial payment
            - 16.3: Display order details including customer name, total amount, and amount paid
        """
        # Get all orders for tenant with customer and order items
        orders = self.db.query(Order).filter(
            Order.tenant_id == tenant_id
        ).all()
        
        result = []
        for order in orders:
            # Get customer details
            customer = self.db.query(Customer).filter(
                Customer.customer_id == order.customer_id
            ).first()
            
            # Get order items to calculate total amount
            order_items = self.db.query(OrderItem).filter(
                OrderItem.order_id == order.order_id
            ).all()
            
            # Calculate total order amount
            total_amount = Decimal('0')
            for order_item in order_items:
                total_amount += order_item.quantity * order_item.selling_price
            
            # Get all payments for this order
            payments = self.db.query(Payment).filter(
                Payment.order_id == order.order_id
            ).all()
            
            # Calculate total amount paid
            amount_paid = Decimal('0')
            for payment in payments:
                amount_paid += payment.amount
            
            # Include order if unpaid or partially paid
            if amount_paid < total_amount:
                result.append({
                    'order_id': order.order_id,
                    'customer_name': customer.name if customer else "Unknown",
                    'customer_phone': customer.phone if customer else "Unknown",
                    'delivery_date': order.delivery_date,
                    'status': order.status,
                    'total_amount': total_amount,
                    'amount_paid': amount_paid,
                    'amount_due': total_amount - amount_paid,
                    'created_at': order.created_at
                })
        
        return result
