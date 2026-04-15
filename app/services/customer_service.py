"""
Customer Service for managing bakery customer operations.

This service handles customer creation, retrieval, search, and listing
with proper tenant isolation and validation.
"""

from typing import List, Optional
from uuid import UUID
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from sqlalchemy import or_, func

from app.models import Customer


class CustomerService:
    """
    Service for managing customer operations.
    
    Handles customer creation, search, retrieval, and listing with
    tenant isolation and duplicate phone number validation.
    """
    
    def __init__(self, db: Session):
        """
        Initialize CustomerService with database session.
        
        Args:
            db: SQLAlchemy database session
        """
        self.db = db
    
    def create_customer(
        self,
        tenant_id: UUID,
        name: str,
        phone: str
    ) -> Customer:
        """
        Create a new customer with validation.
        
        Validates that name and phone are provided, checks for duplicate
        phone numbers within the tenant, and creates the customer record.
        
        Args:
            tenant_id: UUID of the tenant
            name: Customer name
            phone: Customer phone number
        
        Returns:
            Customer: The newly created customer
        
        Raises:
            ValueError: If name or phone is missing, or if phone already exists
        
        Requirements:
            - 2.2: Validate that both name and phone number are provided
            - 2.3: Request missing information if name or phone is missing
            - 2.4: Check if phone number already exists for the Tenant
            - 2.5: Return error message for duplicate customer
            - 2.6: Create Customer record with all required fields
            - 2.7: Confirm customer was added
        """
        # Validate required fields
        if not name or not name.strip():
            raise ValueError("Customer name is required")
        
        if not phone or not phone.strip():
            raise ValueError("Customer phone number is required")
        
        # Clean inputs
        name = name.strip()
        phone = phone.strip()
        
        # Check for duplicate phone number
        existing_customer = self.db.query(Customer).filter(
            Customer.tenant_id == tenant_id,
            Customer.phone == phone
        ).first()
        
        if existing_customer:
            raise ValueError(
                f"A customer with phone number {phone} already exists: {existing_customer.name}"
            )
        
        # Create customer
        try:
            customer = Customer(
                tenant_id=tenant_id,
                name=name,
                phone=phone
            )
            self.db.add(customer)
            self.db.commit()
            self.db.refresh(customer)
            return customer
        except IntegrityError as e:
            self.db.rollback()
            # Handle race condition where another process created the customer
            existing = self.db.query(Customer).filter(
                Customer.tenant_id == tenant_id,
                Customer.phone == phone
            ).first()
            if existing:
                raise ValueError(
                    f"A customer with phone number {phone} already exists: {existing.name}"
                )
            raise
    
    def get_customer(
        self,
        tenant_id: UUID,
        search: str
    ) -> List[Customer]:
        """
        Retrieve customers by name or phone search.
        
        Supports fuzzy name matching and exact phone matching.
        Returns all matching customers for disambiguation.
        
        Args:
            tenant_id: UUID of the tenant
            search: Search string (name or phone)
        
        Returns:
            List[Customer]: List of matching customers (may be empty)
        
        Requirements:
            - 3.1: Extract search criteria (name or phone)
            - 3.2: Query Customers filtered by Tenant_ID and search criteria
            - 3.3: Display customer details if exactly one match
            - 3.4: Display all matching customers for disambiguation
            - 3.5: Return message if no customer matches
            - 3.6: Treat phone number as unique identifier per Tenant
        """
        if not search or not search.strip():
            return []
        
        search = search.strip()
        
        # Search by phone (exact match) or name (case-insensitive partial match)
        query = self.db.query(Customer).filter(
            Customer.tenant_id == tenant_id
        ).filter(
            or_(
                Customer.phone == search,
                func.lower(Customer.name).contains(func.lower(search))
            )
        )
        
        return query.order_by(Customer.name).all()
    
    def list_customers(self, tenant_id: UUID) -> List[Customer]:
        """
        List all customers for a tenant.
        
        Returns all customers sorted by name.
        
        Args:
            tenant_id: UUID of the tenant
        
        Returns:
            List[Customer]: List of all customers for the tenant
        
        Requirements:
            - 4.1: Retrieve all Customers for the Tenant_ID sorted by name
        """
        return self.db.query(Customer).filter(
            Customer.tenant_id == tenant_id
        ).order_by(Customer.name).all()
    
    def get_customer_by_id(
        self,
        tenant_id: UUID,
        customer_id: UUID
    ) -> Optional[Customer]:
        """
        Retrieve a specific customer by ID with tenant isolation.
        
        Args:
            tenant_id: UUID of the tenant
            customer_id: UUID of the customer
        
        Returns:
            Optional[Customer]: The customer if found, None otherwise
        """
        return self.db.query(Customer).filter(
            Customer.tenant_id == tenant_id,
            Customer.customer_id == customer_id
        ).first()
    
    def get_customer_by_phone(
        self,
        tenant_id: UUID,
        phone: str
    ) -> Optional[Customer]:
        """
        Retrieve a customer by phone number with tenant isolation.
        
        Args:
            tenant_id: UUID of the tenant
            phone: Customer phone number
        
        Returns:
            Optional[Customer]: The customer if found, None otherwise
        """
        if not phone or not phone.strip():
            return None
        
        return self.db.query(Customer).filter(
            Customer.tenant_id == tenant_id,
            Customer.phone == phone.strip()
        ).first()
