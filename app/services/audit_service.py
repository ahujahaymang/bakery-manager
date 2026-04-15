"""
Audit Service for logging data modifications.

This service handles audit logging for UPDATE and DELETE operations
with proper tenant isolation.
"""

from typing import Dict, Any, Optional
from uuid import UUID
from datetime import datetime
from sqlalchemy.orm import Session
import json

from app.models import AuditLog


class AuditService:
    """
    Service for audit logging operations.
    
    Handles logging of data modifications (UPDATE and DELETE operations)
    with tenant isolation.
    """
    
    def __init__(self, db: Session):
        """
        Initialize AuditService with database session.
        
        Args:
            db: SQLAlchemy database session
        """
        self.db = db
    
    def log_change(
        self,
        tenant_id: UUID,
        table_name: str,
        record_id: UUID,
        operation_type: str,
        old_values: Optional[Dict[str, Any]] = None,
        new_values: Optional[Dict[str, Any]] = None
    ) -> AuditLog:
        """
        Log a data modification operation.
        
        Creates an audit log entry for UPDATE or DELETE operations. Stores
        old and new values as JSONB for tracking changes.
        
        Args:
            tenant_id: UUID of the tenant
            table_name: Name of the table being modified
            record_id: UUID of the record being modified
            operation_type: Type of operation ("UPDATE" or "DELETE")
            old_values: Dictionary of old field values (optional)
            new_values: Dictionary of new field values (optional)
        
        Returns:
            AuditLog: The newly created audit log entry
        
        Raises:
            ValueError: If validation fails
        
        Requirements:
            - 21.1: Create audit_log record for UPDATE or DELETE operations
            - 21.2: Store Tenant_ID, table_name, record_id, operation_type, old_values, new_values, and timestamp
            - 21.3: Log inventory updates with quantity and cost changes
            - 21.4: Log order status changes
        """
        # Validate operation_type
        valid_operations = ["UPDATE", "DELETE"]
        if operation_type not in valid_operations:
            raise ValueError(
                f"Invalid operation_type '{operation_type}'. "
                f"Valid operations are: {', '.join(valid_operations)}"
            )
        
        # Validate required fields
        if not table_name or not table_name.strip():
            raise ValueError("Table name is required")
        
        if record_id is None:
            raise ValueError("Record ID is required")
        
        # Convert dictionaries to JSON strings for JSONB storage
        old_values_json = json.dumps(old_values) if old_values else None
        new_values_json = json.dumps(new_values) if new_values else None
        
        # Create audit log entry
        audit_log = AuditLog(
            tenant_id=tenant_id,
            table_name=table_name.strip(),
            record_id=record_id,
            operation_type=operation_type,
            old_values=old_values_json,
            new_values=new_values_json
        )
        self.db.add(audit_log)
        
        # Commit transaction
        self.db.commit()
        self.db.refresh(audit_log)
        
        return audit_log
