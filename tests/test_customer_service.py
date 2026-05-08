"""Tests for CustomerService."""

import pytest
from app.services.customer_service import CustomerService


class TestCreateCustomer:
    def test_creates_customer(self, db, tenant_id):
        svc = CustomerService(db)
        c = svc.create_customer(tenant_id, "Alice", "9876543210")
        assert c.name == "Alice"
        assert c.phone == "9876543210"
        assert c.address is None

    def test_creates_customer_with_address(self, db, tenant_id):
        svc = CustomerService(db)
        c = svc.create_customer(tenant_id, "Alice", "9876543210", address="123 Main St")
        assert c.address == "123 Main St"

    def test_rejects_duplicate_phone(self, db, tenant_id):
        svc = CustomerService(db)
        svc.create_customer(tenant_id, "Alice", "9876543210")
        with pytest.raises(ValueError, match="already exists"):
            svc.create_customer(tenant_id, "Alice2", "9876543210")

    def test_allows_same_phone_different_tenant(self, db, tenant_id):
        from app.models import Tenant
        other = Tenant(chat_id="other_chat")
        db.add(other)
        db.commit()
        svc = CustomerService(db)
        svc.create_customer(tenant_id, "Alice", "9876543210")
        c2 = svc.create_customer(other.tenant_id, "Alice", "9876543210")
        assert c2.tenant_id == other.tenant_id

    def test_rejects_empty_name(self, db, tenant_id):
        svc = CustomerService(db)
        with pytest.raises(ValueError):
            svc.create_customer(tenant_id, "", "9876543210")

    def test_rejects_empty_phone(self, db, tenant_id):
        svc = CustomerService(db)
        with pytest.raises(ValueError):
            svc.create_customer(tenant_id, "Alice", "")

    def test_strips_whitespace(self, db, tenant_id):
        svc = CustomerService(db)
        c = svc.create_customer(tenant_id, "  Alice  ", "  9876543210  ")
        assert c.name == "Alice"
        assert c.phone == "9876543210"


class TestGetCustomer:
    def test_finds_by_phone_exact(self, db, tenant_id):
        svc = CustomerService(db)
        svc.create_customer(tenant_id, "Alice", "9876543210")
        results = svc.get_customer(tenant_id, "9876543210")
        assert len(results) == 1
        assert results[0].name == "Alice"

    def test_finds_by_name_partial(self, db, tenant_id):
        svc = CustomerService(db)
        svc.create_customer(tenant_id, "Alice Smith", "9876543210")
        results = svc.get_customer(tenant_id, "alice")
        assert len(results) == 1

    def test_case_insensitive_name(self, db, tenant_id):
        svc = CustomerService(db)
        svc.create_customer(tenant_id, "Alice", "9876543210")
        assert len(svc.get_customer(tenant_id, "ALICE")) == 1

    def test_returns_multiple_matches(self, db, tenant_id):
        svc = CustomerService(db)
        svc.create_customer(tenant_id, "Alice A", "1111111111")
        svc.create_customer(tenant_id, "Alice B", "2222222222")
        results = svc.get_customer(tenant_id, "Alice")
        assert len(results) == 2

    def test_returns_empty_for_no_match(self, db, tenant_id):
        svc = CustomerService(db)
        assert svc.get_customer(tenant_id, "NoOne") == []

    def test_tenant_isolation(self, db, tenant_id):
        from app.models import Tenant
        other = Tenant(chat_id="other_chat")
        db.add(other)
        db.commit()
        svc = CustomerService(db)
        svc.create_customer(tenant_id, "Alice", "9876543210")
        assert svc.get_customer(other.tenant_id, "Alice") == []


class TestListCustomers:
    def test_lists_all(self, db, tenant_id):
        svc = CustomerService(db)
        svc.create_customer(tenant_id, "Bob", "1111111111")
        svc.create_customer(tenant_id, "Alice", "2222222222")
        customers = svc.list_customers(tenant_id)
        assert len(customers) == 2
        assert customers[0].name == "Alice"  # sorted by name

    def test_empty_for_no_customers(self, db, tenant_id):
        svc = CustomerService(db)
        assert svc.list_customers(tenant_id) == []

    def test_tenant_isolation(self, db, tenant_id):
        from app.models import Tenant
        other = Tenant(chat_id="other_chat")
        db.add(other)
        db.commit()
        svc = CustomerService(db)
        svc.create_customer(tenant_id, "Alice", "9876543210")
        assert svc.list_customers(other.tenant_id) == []


class TestGetById:
    def test_finds_by_id(self, db, tenant_id):
        svc = CustomerService(db)
        c = svc.create_customer(tenant_id, "Alice", "9876543210")
        found = svc.get_customer_by_id(tenant_id, c.customer_id)
        assert found.name == "Alice"

    def test_tenant_isolation(self, db, tenant_id):
        from app.models import Tenant
        other = Tenant(chat_id="other_chat")
        db.add(other)
        db.commit()
        svc = CustomerService(db)
        c = svc.create_customer(tenant_id, "Alice", "9876543210")
        assert svc.get_customer_by_id(other.tenant_id, c.customer_id) is None


class TestUpdateAddress:
    def test_updates_address(self, db, tenant_id):
        svc = CustomerService(db)
        c = svc.create_customer(tenant_id, "Alice", "9876543210")
        updated = svc.update_customer_address(tenant_id, c.customer_id, "456 New St")
        assert updated.address == "456 New St"

    def test_clears_address(self, db, tenant_id):
        svc = CustomerService(db)
        c = svc.create_customer(tenant_id, "Alice", "9876543210", address="123 Old St")
        updated = svc.update_customer_address(tenant_id, c.customer_id, None)
        assert updated.address is None
