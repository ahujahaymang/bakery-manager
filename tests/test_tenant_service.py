"""Tests for TenantService."""

import pytest
from app.services.tenant_service import TenantService


class TestGetOrCreateTenant:
    def test_creates_new_tenant(self, db):
        svc = TenantService(db)
        tenant = svc.get_or_create_tenant("chat_001")
        assert tenant.chat_id == "chat_001"
        assert tenant.tenant_id is not None

    def test_returns_existing_tenant(self, db):
        svc = TenantService(db)
        t1 = svc.get_or_create_tenant("chat_001")
        t2 = svc.get_or_create_tenant("chat_001")
        assert t1.tenant_id == t2.tenant_id

    def test_different_chats_get_different_tenants(self, db):
        svc = TenantService(db)
        t1 = svc.get_or_create_tenant("chat_001")
        t2 = svc.get_or_create_tenant("chat_002")
        assert t1.tenant_id != t2.tenant_id

    def test_rejects_empty_chat_id(self, db):
        svc = TenantService(db)
        with pytest.raises(ValueError):
            svc.get_or_create_tenant("")

    def test_strips_whitespace(self, db):
        svc = TenantService(db)
        t1 = svc.get_or_create_tenant("  chat_001  ")
        t2 = svc.get_or_create_tenant("chat_001")
        assert t1.tenant_id == t2.tenant_id
