"""
Tenant Service for managing bakery tenant operations.

This service handles tenant resolution and creation, ensuring that each
Telegram chat_id is mapped to a unique tenant with proper data isolation.
"""

from typing import Optional
from uuid import UUID
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.models import Tenant


class TenantService:
    """
    Service for managing tenant operations.
    
    Handles tenant creation and resolution from Telegram chat_id.
    Ensures proper tenant isolation and automatic onboarding.
    """
    
    def __init__(self, db: Session):
        """
        Initialize TenantService with database session.
        
        Args:
            db: SQLAlchemy database session
        """
        self.db = db
    
    def get_or_create_tenant(self, chat_id: str) -> Tenant:
        """
        Get existing tenant or create new one for the given chat_id.
        
        This method implements automatic tenant onboarding. When a message
        is received from an unknown chat_id, a new tenant is automatically
        created with a generated UUID.
        
        Args:
            chat_id: Telegram chat_id (unique identifier for the chat)
        
        Returns:
            Tenant: The existing or newly created tenant
        
        Raises:
            ValueError: If chat_id is empty or invalid
        
        Requirements:
            - 1.1: Create new Tenant record for unknown Chat_ID
            - 1.2: Link Chat_ID to Tenant_ID
            - 1.3: Store Tenant_ID as UUID
            - 1.4: Store created_at and updated_at timestamps
            - 19.2: Derive Tenant_ID from Chat_ID
            - 19.4: Validate Tenant_ID exists
        """
        if not chat_id or not chat_id.strip():
            raise ValueError("chat_id cannot be empty")
        
        # Try to get existing tenant
        tenant = self.get_tenant_by_chat_id(chat_id)
        
        if tenant:
            return tenant
        
        # Create new tenant if not found
        try:
            tenant = Tenant(chat_id=chat_id.strip())
            self.db.add(tenant)
            self.db.commit()
            self.db.refresh(tenant)
            return tenant
        except IntegrityError:
            # Handle race condition where another process created the tenant
            self.db.rollback()
            tenant = self.get_tenant_by_chat_id(chat_id)
            if tenant:
                return tenant
            raise
    
    def get_tenant_by_chat_id(self, chat_id: str) -> Optional[Tenant]:
        """
        Retrieve tenant by chat_id.
        
        Args:
            chat_id: Telegram chat_id to look up
        
        Returns:
            Optional[Tenant]: The tenant if found, None otherwise
        
        Raises:
            ValueError: If chat_id is empty or invalid
        
        Requirements:
            - 19.2: Derive Tenant_ID from Chat_ID
            - 19.4: Validate Tenant_ID exists
        """
        if not chat_id or not chat_id.strip():
            raise ValueError("chat_id cannot be empty")
        
        return self.db.query(Tenant).filter(
            Tenant.chat_id == chat_id.strip()
        ).first()
    
    def get_tenant_by_id(self, tenant_id) -> Optional[Tenant]:
        """
        Retrieve tenant by tenant_id.
        
        Args:
            tenant_id: UUID or string representation of the tenant
        
        Returns:
            Optional[Tenant]: The tenant if found, None otherwise
        
        Requirements:
            - 19.4: Validate Tenant_ID exists
        """
        return self.db.query(Tenant).filter(
            Tenant.tenant_id == tenant_id
        ).first()

    def set_business_name(self, tenant_id, business_name: str) -> Tenant:
        """Set or update the business name for a tenant."""
        tenant = self.get_tenant_by_id(tenant_id)
        if not tenant:
            raise ValueError(f"Tenant not found: {tenant_id}")
        tenant.business_name = business_name.strip()
        self.db.commit()
        self.db.refresh(tenant)
        return tenant

    def set_country(self, tenant_id, country: str) -> Tenant:
        """Set or update the country for a tenant."""
        tenant = self.get_tenant_by_id(tenant_id)
        if not tenant:
            raise ValueError(f"Tenant not found: {tenant_id}")
        tenant.country = country.strip()
        self.db.commit()
        self.db.refresh(tenant)
        return tenant

    @staticmethod
    def currency_for_country(country: str) -> str:
        """Return the currency symbol/prefix for a given country name."""
        mapping = {
            "india": "Rs.",
            "us": "$", "usa": "$", "united states": "$", "america": "$",
            "uk": "£", "united kingdom": "£", "britain": "£",
            "eu": "€", "europe": "€", "germany": "€", "france": "€",
            "italy": "€", "spain": "€", "netherlands": "€",
            "canada": "CA$",
            "australia": "A$",
            "uae": "AED",
            "singapore": "S$",
        }
        return mapping.get(country.lower().strip(), "Rs.")

    # ── Subscription management ────────────────────────────────────────────

    TRIAL_DAYS = 7

    def start_trial(self, tenant_id) -> Tenant:
        """Start the 7-day free trial for a tenant."""
        from datetime import timedelta
        tenant = self.get_tenant_by_id(tenant_id)
        if not tenant:
            raise ValueError(f"Tenant not found: {tenant_id}")
        now = datetime.utcnow()
        tenant.subscription_status = "trial"
        tenant.trial_started_at = now
        tenant.subscription_expires_at = now + timedelta(days=self.TRIAL_DAYS)
        self.db.commit()
        self.db.refresh(tenant)
        return tenant

    def activate_subscription(self, tenant_id, days: int = 30) -> Tenant:
        """
        Activate or extend a paid subscription.

        Args:
            tenant_id: Tenant UUID
            days: Number of days to grant (default 30)
        """
        from datetime import timedelta
        tenant = self.get_tenant_by_id(tenant_id)
        if not tenant:
            raise ValueError(f"Tenant not found: {tenant_id}")
        now = datetime.utcnow()
        # Extend from current expiry if still active, otherwise from now
        base = tenant.subscription_expires_at if (
            tenant.subscription_expires_at and tenant.subscription_expires_at > now
        ) else now
        tenant.subscription_status = "active"
        tenant.subscription_expires_at = base + timedelta(days=days)
        self.db.commit()
        self.db.refresh(tenant)
        return tenant

    def check_and_update_status(self, tenant_id) -> str:
        """
        Check subscription status, auto-expire if past expiry date.

        Returns:
            str: current status — "pending" | "trial" | "active" | "expired"
        """
        tenant = self.get_tenant_by_id(tenant_id)
        if not tenant:
            return "pending"

        status = tenant.subscription_status

        # Auto-expire if past expiry date
        if status in ("trial", "active") and tenant.subscription_expires_at:
            if datetime.utcnow() > tenant.subscription_expires_at:
                tenant.subscription_status = "expired"
                self.db.commit()
                return "expired"

        return status

    def days_remaining(self, tenant_id) -> Optional[int]:
        """Return days remaining in trial/subscription, or None if not applicable."""
        tenant = self.get_tenant_by_id(tenant_id)
        if not tenant or not tenant.subscription_expires_at:
            return None
        delta = tenant.subscription_expires_at - datetime.utcnow()
        return max(0, delta.days)
