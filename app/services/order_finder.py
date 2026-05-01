"""
Order Finder Helper for centralized order lookup logic.

This module provides utilities for finding orders by various criteria
and formatting order lists for display.
"""

from typing import List, Optional, Tuple
from uuid import UUID
from datetime import date
import logging

from app.models import Order, OrderItem, Customer

logger = logging.getLogger(__name__)


class OrderFinder:
    """
    Helper class for finding orders by various criteria.
    
    Centralizes order lookup logic to eliminate duplication between
    cancel_order and delete_order handlers.
    """
    
    def __init__(self, db):
        """
        Initialize OrderFinder with database session.
        
        Args:
            db: SQLAlchemy database session
        """
        self.db = db
    
    def find_by_id(self, tenant_id: UUID, order_id: UUID) -> Optional[Order]:
        """
        Find order by ID.
        
        Args:
            tenant_id: Tenant UUID
            order_id: Order UUID
        
        Returns:
            Order object or None if not found
        """
        return self.db.query(Order).filter(
            Order.order_id == order_id,
            Order.tenant_id == tenant_id
        ).first()
    
    def find_by_customer_and_date(
        self,
        tenant_id: UUID,
        customer_id: UUID,
        delivery_date: date
    ) -> Optional[Order]:
        """
        Find order by customer and delivery date.
        
        Args:
            tenant_id: Tenant UUID
            customer_id: Customer UUID
            delivery_date: Delivery date
        
        Returns:
            Order object or None if not found
        """
        return self.db.query(Order).filter(
            Order.tenant_id == tenant_id,
            Order.customer_id == customer_id,
            Order.delivery_date == delivery_date
        ).first()
    
    def find_by_customer(
        self,
        tenant_id: UUID,
        customer_id: UUID,
        status_filter: Optional[str] = None
    ) -> List[Order]:
        """
        Find all orders for a customer.
        
        Args:
            tenant_id: Tenant UUID
            customer_id: Customer UUID
            status_filter: Optional status filter (e.g., 'pending')
        
        Returns:
            List of Order objects
        """
        query = self.db.query(Order).filter(
            Order.tenant_id == tenant_id,
            Order.customer_id == customer_id
        )
        
        if status_filter:
            query = query.filter(Order.status == status_filter)
        
        return query.order_by(Order.delivery_date.desc()).all()
    
    def format_order_list_for_selection(
        self,
        orders: List[Order],
        customer_name: str
    ) -> str:
        """
        Format a list of orders for user selection.
        
        Args:
            orders: List of Order objects
            customer_name: Name of the customer
        
        Returns:
            Formatted string with numbered order list
        """
        result = f"🤔 {customer_name} has multiple orders:\n\n"
        
        for idx, order in enumerate(orders, 1):
            # Get order items to show what was ordered
            order_items = self.db.query(OrderItem).filter(
                OrderItem.order_id == order.order_id
            ).all()
            
            items_desc = ", ".join([
                f"{item.recipe_name} x{item.quantity}"
                for item in order_items[:2]
            ])
            
            if len(order_items) > 2:
                items_desc += f" +{len(order_items)-2} more"
            
            result += f"{idx}. *{order.delivery_date}* - {items_desc}"
            
            # Add status if not pending
            if order.status != 'pending':
                result += f" ({order.status})"
            
            result += "\n"
        
        return result
    
    def get_order_details_for_display(self, order: Order) -> Tuple[str, str]:
        """
        Get order details for display.
        
        Args:
            order: Order object
        
        Returns:
            Tuple of (customer_name, items_description)
        """
        # Get customer name
        customer = self.db.query(Customer).filter(
            Customer.customer_id == order.customer_id
        ).first()
        customer_name = customer.name if customer else "Unknown"
        
        # Get order items
        order_items = self.db.query(OrderItem).filter(
            OrderItem.order_id == order.order_id
        ).all()
        
        items_desc = ", ".join([
            f"{item.recipe_name} x{item.quantity}"
            for item in order_items
        ])
        
        return customer_name, items_desc
