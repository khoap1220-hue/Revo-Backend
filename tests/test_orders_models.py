"""
Tests for Order and OrderItem models, temporary shopping cart (status=draft),
subtotal/total calculations, and order status transitions (#82).
"""

from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.orders import Order, OrderItem, OrderStatus
from app.models.users.user import User, UserType


class TestOrderAndCartModels:
    """Test suite for Order, OrderItem, temporary cart operations, and status transitions."""

    async def test_buyer_can_create_draft_cart_and_persist_order(self, db_session: AsyncSession):
        """Buyers can create orders starting as a temporary cart (status=draft)."""
        buyer = User(
            email=f"buyer_{uuid4().hex[:8]}@example.com",
            password_hash="hashed_password",
            user_type=UserType.BUYER,
            is_active=True,
            is_verified=True,
        )
        db_session.add(buyer)
        await db_session.commit()
        await db_session.refresh(buyer)

        order = Order(
            buyer_id=buyer.id,
            shipping_address="123 Avenida Central, San Jose, Costa Rica",
        )
        assert order.status == OrderStatus.DRAFT
        assert order.is_cart is True
        assert order.total_amount == Decimal("0.00")

        product_a = uuid4()
        product_b = uuid4()
        order.add_item(product_id=product_a, quantity=2, unit_price=Decimal("12.50"))
        order.add_item(product_id=product_b, quantity=3, unit_price=Decimal("5.25"))

        assert len(order.items) == 2
        assert order.total_amount == Decimal("40.75")

        db_session.add(order)
        await db_session.commit()
        await db_session.refresh(order)

        assert order.id is not None
        assert order.buyer_id == buyer.id
        assert order.status == OrderStatus.DRAFT
        assert order.total_amount == Decimal("40.75")

    def test_order_items_calculated_correctly_and_cart_mutations(self):
        """Order items and cart quantity updates recalculate totals accurately."""
        buyer_id = uuid4()
        product_1 = uuid4()
        product_2 = uuid4()

        cart = Order(buyer_id=buyer_id, shipping_address="Heredia, Costa Rica")
        item1 = cart.add_item(product_id=product_1, quantity=2, unit_price="15.00")
        assert item1.subtotal == Decimal("30.00")
        assert cart.total_amount == Decimal("30.00")

        # Adding same product increments quantity and updates unit_price
        cart.add_item(product_id=product_1, quantity=1, unit_price="15.00")
        assert item1.quantity == 3
        assert item1.subtotal == Decimal("45.00")
        assert cart.total_amount == Decimal("45.00")

        # Add second item
        cart.add_item(product_id=product_2, quantity=4, unit_price="2.50")
        assert cart.total_amount == Decimal("55.00")

        # Update quantity of first item
        cart.update_item_quantity(product_id=product_1, quantity=1)
        assert cart.total_amount == Decimal("25.00")

        # Remove second item
        removed = cart.remove_item(product_id=product_2)
        assert removed is True
        assert cart.total_amount == Decimal("15.00")

    def test_order_status_transitions_work_as_expected(self):
        """Valid lifecycle transitions succeed and invalid transitions raise ValueError."""
        order = Order(
            buyer_id=uuid4(),
            shipping_address="Cartago, Costa Rica",
        )
        order.add_item(product_id=uuid4(), quantity=2, unit_price="10.00")

        # Valid lifecycle: draft -> pending -> confirmed -> shipped -> delivered
        assert order.transition_to(OrderStatus.PENDING) == OrderStatus.PENDING
        assert order.is_cart is False

        # Cannot mutate cart items once order leaves draft
        with pytest.raises(ValueError, match="draft"):
            order.add_item(product_id=uuid4(), quantity=1, unit_price="5.00")

        assert order.transition_to(OrderStatus.CONFIRMED) == OrderStatus.CONFIRMED
        assert order.transition_to(OrderStatus.SHIPPED) == OrderStatus.SHIPPED
        assert order.transition_to(OrderStatus.DELIVERED) == OrderStatus.DELIVERED

        # Cannot transition from delivered to cancelled or pending
        with pytest.raises(ValueError, match="Invalid order status transition"):
            order.transition_to(OrderStatus.CANCELLED)

    def test_checkout_validation_and_cancellation(self):
        """Empty cart or missing shipping address cannot transition from draft to pending."""
        empty_cart = Order(buyer_id=uuid4(), shipping_address="Alajuela, Costa Rica")
        with pytest.raises(ValueError, match="empty draft cart"):
            empty_cart.transition_to(OrderStatus.PENDING)

        no_address_cart = Order(buyer_id=uuid4())
        no_address_cart.add_item(product_id=uuid4(), quantity=1, unit_price="8.00")
        with pytest.raises(ValueError, match="Shipping address is required"):
            no_address_cart.transition_to(OrderStatus.PENDING)

        # Cancellation from pending succeeds
        no_address_cart.shipping_address = "Alajuela, Costa Rica"
        no_address_cart.transition_to(OrderStatus.PENDING)
        assert no_address_cart.transition_to(OrderStatus.CANCELLED) == OrderStatus.CANCELLED

    def test_order_item_validation_guards(self):
        """OrderItem rejects non-positive quantities and negative unit prices."""
        with pytest.raises(ValueError, match="quantity must be greater than zero"):
            OrderItem(order_id=uuid4(), product_id=uuid4(), quantity=0, unit_price="10.00")

        with pytest.raises(ValueError, match="unit_price cannot be negative"):
            OrderItem(order_id=uuid4(), product_id=uuid4(), quantity=1, unit_price="-5.00")
