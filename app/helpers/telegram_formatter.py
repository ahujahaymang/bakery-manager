"""
Telegram Formatter for consistent message formatting.

This module provides utilities for formatting various types of messages
for Telegram, separating presentation logic from business logic.
"""

from typing import List, Dict, Any
from decimal import Decimal
from datetime import date


class TelegramFormatter:
    """
    Helper class for formatting Telegram messages.
    
    Provides consistent formatting for orders, customers, inventory,
    and other entities across all handlers.
    """
    
    @staticmethod
    def format_order_created(
        order: Any,
        items: List[Any],
        customer_name: str = None,
        customer_phone: str = None,
        missing_recipes: List[str] = None
    ) -> str:
        """
        Format order creation success message.
        
        Args:
            order: Order object
            items: List of OrderItemCreate objects
            customer_name: Optional customer name
            customer_phone: Optional customer phone
            missing_recipes: Optional list of missing recipe names
        
        Returns:
            Formatted message string
        """
        result = "✅ Order created"
        
        if customer_name and customer_phone:
            result += f" for *{customer_name}* ({customer_phone})"
        
        result += f"!\n\n*Delivery Date:* {order.delivery_date}\n*Status:* {order.status}\n\n*Items:*\n"
        
        for item in items:
            result += f"• {item.recipe_name} x{item.quantity} @ ₹{item.selling_price}\n"
        
        # Add warning for missing recipes
        if missing_recipes:
            result += f"\n⚠️ *Warning:* The following recipes don't exist:\n"
            for recipe_name in missing_recipes:
                result += f"• {recipe_name}\n"
            result += f"\n💡 Add the recipe for better tracking!"
        
        return result
    
    @staticmethod
    def format_order_cancelled(
        customer_name: str,
        delivery_date: date,
        payment_warning: str = None
    ) -> str:
        """
        Format order cancellation message.
        
        Args:
            customer_name: Customer name
            delivery_date: Delivery date
            payment_warning: Optional payment warning message
        
        Returns:
            Formatted message string
        """
        result = f"✅ Order cancelled!\n\n*Customer:* {customer_name}\n*Delivery Date:* {delivery_date}\n*Status:* Cancelled"
        
        if payment_warning:
            result += payment_warning
        
        return result
    
    @staticmethod
    def format_order_deleted(
        customer_name: str,
        delivery_date: date,
        items_desc: str
    ) -> str:
        """
        Format order deletion message.
        
        Args:
            customer_name: Customer name
            delivery_date: Delivery date
            items_desc: Description of order items
        
        Returns:
            Formatted message string
        """
        return (
            f"✅ Order permanently deleted!\n\n"
            f"*Customer:* {customer_name}\n"
            f"*Delivery Date:* {delivery_date}\n"
            f"*Items:* {items_desc}\n\n"
            f"💡 This order has been removed from the database."
        )
    
    @staticmethod
    def format_customer_not_found_with_phone_request(
        customer_name: str,
        items_desc: str = None
    ) -> str:
        """
        Format message requesting phone number for new customer.
        
        Args:
            customer_name: Customer name
            items_desc: Optional description of order items
        
        Returns:
            Formatted message string
        """
        result = f"❌ Customer '*{customer_name}*' not found.\n\n"
        result += f"Please provide {customer_name}'s phone number to add them as a customer.\n\n"
        result += f"Example: 9876543210"
        return result
    
    @staticmethod
    def format_delivery_date_request(
        customer_name: str,
        items_desc: str
    ) -> str:
        """
        Format message requesting delivery date.
        
        Args:
            customer_name: Customer name
            items_desc: Description of order items
        
        Returns:
            Formatted message string
        """
        return (
            f"📅 When should this order be delivered?\n\n"
            f"*Customer:* {customer_name}\n"
            f"*Items:* {items_desc}\n\n"
            f"Please provide the delivery date.\n"
            f"Example: \"tomorrow\", \"April 20\", or \"2026-04-20\""
        )
    
    @staticmethod
    def format_customer_added_and_order_created(
        customer: Any,
        order: Any,
        items: List[Any],
        missing_recipes: List[str] = None
    ) -> str:
        """
        Format message for customer creation and order creation.
        
        Args:
            customer: Customer object
            order: Order object
            items: List of OrderItemCreate objects
            missing_recipes: Optional list of missing recipe names
        
        Returns:
            Formatted message string
        """
        result = f"✅ Customer added: {customer.name} ({customer.phone})\n\n"
        result += TelegramFormatter.format_order_created(order, items, missing_recipes=missing_recipes)
        return result
    
    @staticmethod
    def format_error_cannot_delete_order_with_payments(total_paid: Decimal) -> str:
        """
        Format error message for attempting to delete order with payments.
        
        Args:
            total_paid: Total amount paid
        
        Returns:
            Formatted error message
        """
        return (
            f"❌ Cannot delete order with payments!\n\n"
            f"This order has ₹{total_paid} in payments recorded.\n\n"
            f"💡 *Tip:* If the customer cancelled, use 'cancel order' instead to keep it for records."
        )
    
    @staticmethod
    def format_payment_warning(total_paid: Decimal) -> str:
        """
        Format payment warning for order cancellation.
        
        Args:
            total_paid: Total amount paid
        
        Returns:
            Formatted warning message
        """
        return f"\n\n⚠️ *Note:* This order has ₹{total_paid} in payments. Please process refund separately."
