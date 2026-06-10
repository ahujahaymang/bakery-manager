"""
Payment Service for managing payment operations.

This service handles payment recording and payment history retrieval
with proper tenant isolation and validation.
"""

from typing import List, Dict, Any, Optional
from uuid import UUID
from decimal import Decimal
from datetime import date, datetime
from dataclasses import dataclass
from sqlalchemy.orm import Session

from app.models import Payment, Order, Customer, OrderItem


@dataclass
class PaymentCreate:
    """Data class for creating a payment."""
    order_identifier: str  # order_id (UUID) or customer name/phone
    amount: Decimal
    method: str  # Cash, Paytm, Bank Transfer


class PaymentService:
    """
    Service for managing payment operations.
    
    Handles payment recording and payment history retrieval with tenant
    isolation and validation.
    """
    
    def __init__(self, db: Session):
        """
        Initialize PaymentService with database session.
        
        Args:
            db: SQLAlchemy database session
        """
        self.db = db
    
    def record_payment(
        self,
        tenant_id: UUID,
        payment: PaymentCreate
    ) -> Payment:
        """
        Record a payment for an order.
        
        Validates payment method is one of: Cash, Paytm, Bank Transfer.
        Validates amount is positive. Retrieves order by identifier and
        tenant_id. Creates Payment record.
        
        Args:
            tenant_id: UUID of the tenant
            payment: PaymentCreate data with order identifier, amount, and method
        
        Returns:
            Payment: The newly created payment record
        
        Raises:
            ValueError: If validation fails or order is not found
        
        Requirements:
            - 15.1: Extract order identifier, amount, and payment method
            - 15.2: Validate payment method is one of: Cash, Paytm, Bank Transfer
            - 15.3: Retrieve Order by identifier and Tenant_ID
            - 15.4: Create Payment record
            - 15.5: Confirm payment was recorded
        """
        # Validate required fields
        if not payment.order_identifier or not payment.order_identifier.strip():
            raise ValueError("Order identifier is required")
        
        if payment.amount is None:
            raise ValueError("Payment amount is required")
        
        if not payment.method or not payment.method.strip():
            raise ValueError("Payment method is required")
        
        # Validate amount is positive
        try:
            amount = Decimal(str(payment.amount))
            if amount <= 0:
                raise ValueError(
                    f"Payment amount must be positive. You provided: {payment.amount}"
                )
        except (ValueError, TypeError) as e:
            if "positive" in str(e):
                raise
            raise ValueError(
                f"Invalid payment amount: {payment.amount}"
            )
        
        # Validate payment method
        valid_methods = ["Cash", "UPI", "GPay", "PhonePe", "Paytm", "Bank Transfer", "NEFT", "Cheque"]
        method = payment.method.strip()

        # Case-insensitive match so "gpay", "upi" etc. work naturally
        method_lower = method.lower()
        matched = next(
            (m for m in valid_methods if m.lower() == method_lower),
            None
        )
        if not matched:
            raise ValueError(
                f"Invalid payment method '{method}'. "
                f"Valid methods are: {', '.join(valid_methods)}"
            )
        method = matched  # normalise to canonical casing
        
        # Retrieve order by identifier and tenant_id
        # Try UUID first, then customer name/phone
        order = None
        try:
            order_id = UUID(payment.order_identifier.strip())
            order = self.db.query(Order).filter(
                Order.order_id == order_id,
                Order.tenant_id == tenant_id
            ).first()
        except (ValueError, AttributeError):
            # Not a UUID, try to find by customer name or phone
            from app.services.customer_service import CustomerService
            customer_service = CustomerService(self.db)
            
            customers = customer_service.get_customer(tenant_id, payment.order_identifier)
            
            if len(customers) == 0:
                raise ValueError(
                    f"No customer or order found matching '{payment.order_identifier}'. "
                    "Please provide an order ID or customer name/phone."
                )
            elif len(customers) > 1:
                customer_list = [f"{c.name} ({c.phone})" for c in customers]
                raise ValueError(
                    f"Multiple customers match '{payment.order_identifier}': {', '.join(customer_list)}. "
                    "Please be more specific or provide the order ID."
                )
            
            # Single customer found - get their most recent unpaid order
            customer = customers[0]
            
            # Get unpaid orders for this customer
            unpaid_orders = self.db.query(Order).filter(
                Order.tenant_id == tenant_id,
                Order.customer_id == customer.customer_id
            ).all()
            
            # Filter to orders with outstanding balance
            orders_with_balance = []
            for ord in unpaid_orders:
                # Calculate total order amount
                order_items = self.db.query(OrderItem).filter(
                    OrderItem.order_id == ord.order_id
                ).all()
                
                total_amount = Decimal('0')
                for item in order_items:
                    total_amount += item.quantity * item.selling_price
                
                # Calculate amount paid
                payments = self.db.query(Payment).filter(
                    Payment.order_id == ord.order_id
                ).all()
                
                amount_paid = Decimal('0')
                for pmt in payments:
                    amount_paid += pmt.amount
                
                if amount_paid < total_amount:
                    orders_with_balance.append({
                        'order': ord,
                        'total': total_amount,
                        'paid': amount_paid,
                        'due': total_amount - amount_paid
                    })
            
            if len(orders_with_balance) == 0:
                raise ValueError(
                    f"No unpaid orders found for {customer.name}. All orders are fully paid!"
                )
            elif len(orders_with_balance) > 1:
                # Multiple unpaid orders - need disambiguation
                order_list = []
                for ord_info in orders_with_balance:
                    order_list.append(
                        f"Order {ord_info['order'].delivery_date} - Due: ₹{ord_info['due']}"
                    )
                raise ValueError(
                    f"{customer.name} has multiple unpaid orders:\n" + "\n".join(order_list) +
                    "\n\nPlease specify which order or provide the order ID."
                )
            
            # Single unpaid order found
            order = orders_with_balance[0]['order']
        
        if not order:
            raise ValueError(
                f"Order not found. Please check the order ID or customer name."
            )
        
        # Create payment record
        new_payment = Payment(
            tenant_id=tenant_id,
            order_id=order.order_id,
            amount=amount,
            method=method
        )
        self.db.add(new_payment)
        
        # Commit transaction
        self.db.commit()
        self.db.refresh(new_payment)
        
        return new_payment
    
    def get_payment_history(
        self,
        tenant_id: UUID,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None
    ) -> List[Dict[str, Any]]:
        """
        Retrieve payment history for a tenant.
        
        Retrieves all payments for the tenant, optionally filtered by date
        range. Sorted by created_at descending. Includes customer name, order
        reference, amount, method, and date.
        
        Args:
            tenant_id: UUID of the tenant
            start_date: Optional start date for filtering (inclusive)
            end_date: Optional end date for filtering (inclusive)
        
        Returns:
            List[Dict]: List of payments with details
        
        Requirements:
            - 17.1: Retrieve all Payments for Tenant_ID
            - 17.2: Sort Payments by created_at descending
            - 17.3: Display payment details including customer name, order reference, amount, method, and date
            - 17.4: Filter Payments by created_at within date range if specified
        """
        # Build query for payments with tenant isolation
        query = self.db.query(Payment).filter(
            Payment.tenant_id == tenant_id
        )
        
        # Apply date range filters if provided
        if start_date:
            # Filter by created_at >= start_date (beginning of day)
            start_datetime = datetime.combine(start_date, datetime.min.time())
            query = query.filter(Payment.created_at >= start_datetime)
        
        if end_date:
            # Filter by created_at <= end_date (end of day)
            end_datetime = datetime.combine(end_date, datetime.max.time())
            query = query.filter(Payment.created_at <= end_datetime)
        
        # Sort by created_at descending
        payments = query.order_by(Payment.created_at.desc()).all()
        
        result = []
        for payment in payments:
            # Get order details
            order = self.db.query(Order).filter(
                Order.order_id == payment.order_id
            ).first()
            
            # Get customer details
            customer = None
            if order:
                customer = self.db.query(Customer).filter(
                    Customer.customer_id == order.customer_id
                ).first()
            
            result.append({
                'payment_id': payment.payment_id,
                'order_id': payment.order_id,
                'customer_name': customer.name if customer else "Unknown",
                'customer_phone': customer.phone if customer else "Unknown",
                'amount': payment.amount,
                'method': payment.method,
                'payment_date': payment.created_at.date() if payment.created_at else None,
                'created_at': payment.created_at
            })
        
        return result
