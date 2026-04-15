"""
Order Orchestrator for managing order creation workflows.

This orchestrator is platform-agnostic and handles the business logic
for creating orders, including customer disambiguation, date collection,
and recipe disambiguation. Can be used with Telegram, WhatsApp, or any
other messaging platform.
"""

from typing import List, Dict, Any, Optional, Tuple
from decimal import Decimal
from datetime import date
from uuid import UUID
import logging

from app.services import OrderService, CustomerService, RecipeService

logger = logging.getLogger(__name__)


class OrderItemData:
    """Data class for order items."""
    def __init__(self, recipe_name: str, quantity: int, selling_price: float):
        self.recipe_name = recipe_name
        self.quantity = quantity
        self.selling_price = Decimal(str(selling_price))


class OrderOrchestrator:
    """
    Platform-agnostic orchestrator for order creation workflows.
    
    Handles multi-step order creation including:
    - Customer disambiguation
    - Delivery date collection
    - Recipe disambiguation
    - Order creation
    
    Can be used with any messaging platform (Telegram, WhatsApp, etc.)
    """
    
    @staticmethod
    def reconstruct_order_items(items_data: List[Dict[str, Any]]) -> List[OrderItemData]:
        """
        Reconstruct OrderItemData objects from dict data.
        
        Args:
            items_data: List of item dicts with recipe_name, quantity, selling_price
        
        Returns:
            List of OrderItemData objects
        """
        items = []
        for item_dict in items_data:
            items.append(OrderItemData(
                recipe_name=item_dict['recipe_name'],
                quantity=item_dict['quantity'],
                selling_price=item_dict['selling_price']
            ))
        return items
    
    @staticmethod
    def create_order_from_data(
        db,
        tenant_id: UUID,
        customer_identifier: str,
        delivery_date: date,
        items: List[OrderItemData]
    ):
        """
        Create an order from structured data.
        
        Args:
            db: Database session
            tenant_id: Tenant UUID
            customer_identifier: Customer name or phone
            delivery_date: Delivery date
            items: List of OrderItemData objects
        
        Returns:
            Tuple of (order, items) - Created order object and items list
        """
        from dataclasses import dataclass
        
        @dataclass
        class OrderItemCreate:
            recipe_name: str
            quantity: int
            selling_price: Decimal
        
        @dataclass
        class OrderCreate:
            customer_identifier: str
            delivery_date: date
            items: list
        
        order_service = OrderService(db)
        
        order_items = [
            OrderItemCreate(
                recipe_name=item.recipe_name,
                quantity=item.quantity,
                selling_price=item.selling_price
            )
            for item in items
        ]
        
        order_data = OrderCreate(
            customer_identifier=customer_identifier,
            delivery_date=delivery_date,
            items=order_items
        )
        
        order = order_service.create_order(tenant_id, order_data)
        return order, order_items
    
    @staticmethod
    def create_order_from_context(
        db,
        tenant_id: UUID,
        order_data_dict: Dict[str, Any]
    ):
        """
        Create an order from context data dictionary.
        
        Args:
            db: Database session
            tenant_id: Tenant UUID
            order_data_dict: Order data dictionary with customer_identifier,
                           delivery_date, and items
        
        Returns:
            Tuple of (order, items) - Created order object and items list
        """
        items = OrderOrchestrator.reconstruct_order_items(
            order_data_dict.get('items', [])
        )
        
        return OrderOrchestrator.create_order_from_data(
            db=db,
            tenant_id=tenant_id,
            customer_identifier=order_data_dict['customer_identifier'],
            delivery_date=date.fromisoformat(order_data_dict['delivery_date']),
            items=items
        )
    
    @staticmethod
    def find_customers(
        db,
        tenant_id: UUID,
        customer_identifier: str
    ) -> List[Any]:
        """
        Find customers matching an identifier.
        
        Args:
            db: Database session
            tenant_id: Tenant UUID
            customer_identifier: Customer name or phone
        
        Returns:
            List of matching customer objects
        """
        customer_service = CustomerService(db)
        return customer_service.get_customer(tenant_id, customer_identifier)
    
    @staticmethod
    def find_recipes(
        db,
        tenant_id: UUID,
        recipe_name: str
    ) -> List[Any]:
        """
        Find recipes matching a name.
        
        Args:
            db: Database session
            tenant_id: Tenant UUID
            recipe_name: Recipe name to search
        
        Returns:
            List of matching recipe objects
        """
        recipe_service = RecipeService(db)
        return recipe_service.search_recipes(tenant_id, recipe_name)
    
    @staticmethod
    def create_customer(
        db,
        tenant_id: UUID,
        name: str,
        phone: str
    ):
        """
        Create a new customer.
        
        Args:
            db: Database session
            tenant_id: Tenant UUID
            name: Customer name
            phone: Customer phone number
        
        Returns:
            Created customer object
        """
        customer_service = CustomerService(db)
        return customer_service.create_customer(tenant_id, name, phone)
    
    @staticmethod
    def extract_phone_number(text: str) -> Optional[str]:
        """
        Extract phone number from text.
        
        Args:
            text: Input text
        
        Returns:
            Extracted phone number or None if invalid
        """
        phone = ''.join(filter(str.isdigit, text))
        
        if not phone or len(phone) < 10:
            return None
        
        return phone
    
    @staticmethod
    def validate_delivery_date(delivery_date: date) -> Tuple[bool, Optional[str]]:
        """
        Validate delivery date.
        
        Args:
            delivery_date: Date to validate
        
        Returns:
            Tuple of (is_valid, error_message)
        """
        if delivery_date < date.today():
            return False, "Delivery date cannot be in the past. Please provide a future date."
        
        return True, None
    
    @staticmethod
    def get_missing_recipes(order) -> Optional[List[str]]:
        """
        Get list of missing recipes from order.
        
        Args:
            order: Order object
        
        Returns:
            List of missing recipe names or None
        """
        if hasattr(order, '_missing_recipes') and order._missing_recipes:
            return order._missing_recipes
        return None
