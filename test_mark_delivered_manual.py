"""
Manual test to verify mark_delivered implementation.
This test uses the actual models and database to verify the implementation works.
"""

from uuid import uuid4
from decimal import Decimal
from datetime import date, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Tenant, Customer, Recipe, Order, OrderItem
from app.services.order_service import OrderService, OrderCreate, OrderItemCreate


def test_mark_delivered():
    """Test mark_delivered method with actual models."""
    # Create in-memory database
    engine = create_engine("sqlite:///:memory:")
    
    # Create only the tables we need (not audit_logs which has JSONB)
    Tenant.__table__.create(bind=engine, checkfirst=True)
    Customer.__table__.create(bind=engine, checkfirst=True)
    Recipe.__table__.create(bind=engine, checkfirst=True)
    Order.__table__.create(bind=engine, checkfirst=True)
    OrderItem.__table__.create(bind=engine, checkfirst=True)
    
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = SessionLocal()
    
    try:
        # Create tenant
        tenant = Tenant(chat_id="test_chat_123")
        session.add(tenant)
        session.commit()
        session.refresh(tenant)
        
        # Create customer
        customer = Customer(
            tenant_id=tenant.tenant_id,
            name="John Doe",
            phone="1234567890"
        )
        session.add(customer)
        session.commit()
        session.refresh(customer)
        
        # Create recipe
        recipe = Recipe(
            tenant_id=tenant.tenant_id,
            name="Chocolate Cake",
            yield_per_batch=10
        )
        session.add(recipe)
        session.commit()
        session.refresh(recipe)
        
        # Create order service
        order_service = OrderService(session)
        
        # Create an order
        tomorrow = date.today() + timedelta(days=1)
        order_data = OrderCreate(
            customer_identifier=customer.phone,
            delivery_date=tomorrow,
            items=[
                OrderItemCreate(
                    recipe_name=recipe.name,
                    quantity=2,
                    selling_price=Decimal("25.00")
                )
            ]
        )
        order = order_service.create_order(tenant.tenant_id, order_data)
        
        print(f"✓ Created order with ID: {order.order_id}")
        print(f"  Initial status: {order.status}")
        assert order.status == "pending", "Order should start with pending status"
        
        # Mark order as delivered
        updated_order = order_service.mark_delivered(tenant.tenant_id, order.order_id)
        
        print(f"✓ Marked order as delivered")
        print(f"  Updated status: {updated_order.status}")
        assert updated_order.status == "delivered", "Order status should be 'delivered'"
        assert updated_order.order_id == order.order_id, "Order ID should match"
        
        # Test order not found
        fake_order_id = uuid4()
        try:
            order_service.mark_delivered(tenant.tenant_id, fake_order_id)
            print("✗ Should have raised ValueError for non-existent order")
            assert False, "Should have raised ValueError"
        except ValueError as e:
            print(f"✓ Correctly raised ValueError for non-existent order: {e}")
            assert "Order not found" in str(e)
        
        # Test tenant isolation
        tenant2 = Tenant(chat_id="test_chat_456")
        session.add(tenant2)
        session.commit()
        session.refresh(tenant2)
        
        try:
            order_service.mark_delivered(tenant2.tenant_id, order.order_id)
            print("✗ Should have raised ValueError for cross-tenant access")
            assert False, "Should have raised ValueError"
        except ValueError as e:
            print(f"✓ Correctly prevented cross-tenant access: {e}")
            assert "Order not found" in str(e)
        
        print("\n✓ All tests passed!")
        
    finally:
        session.close()


if __name__ == "__main__":
    test_mark_delivered()
