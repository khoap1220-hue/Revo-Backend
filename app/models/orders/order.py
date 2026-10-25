"""
Order and temporary shopping cart model for Farmers Marketplace.
"""

from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import TYPE_CHECKING, Dict, List, Optional, Set, Union
import uuid
from uuid import UUID

from sqlalchemy import DateTime, Enum as SqlEnum, ForeignKey, Numeric, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.orders.order_item import OrderItem


class OrderStatus(str, Enum):
    """Lifecycle states for an order (DRAFT represents a temporary shopping cart)."""

    DRAFT = "draft"
    PENDING = "pending"
    CONFIRMED = "confirmed"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"


ALLOWED_ORDER_TRANSITIONS: Dict[OrderStatus, Set[OrderStatus]] = {
    OrderStatus.DRAFT: {OrderStatus.PENDING, OrderStatus.CANCELLED},
    OrderStatus.PENDING: {OrderStatus.CONFIRMED, OrderStatus.CANCELLED},
    OrderStatus.CONFIRMED: {OrderStatus.SHIPPED, OrderStatus.CANCELLED},
    OrderStatus.SHIPPED: {OrderStatus.DELIVERED},
    OrderStatus.DELIVERED: set(),
    OrderStatus.CANCELLED: set(),
}


def _to_money(value: Union[Decimal, float, int, str]) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class Order(Base):
    """
    Order model supporting both temporary shopping carts (status=OrderStatus.DRAFT)
    and confirmed buyer orders through delivery.
    """

    __tablename__ = "orders"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    buyer_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[OrderStatus] = mapped_column(
        SqlEnum(
            OrderStatus,
            name="order_status",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        default=OrderStatus.DRAFT,
        nullable=False,
        index=True,
    )
    total_amount: Mapped[Decimal] = mapped_column(
        Numeric(10, 2),
        default=Decimal("0.00"),
        nullable=False,
    )
    shipping_address: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        server_default=func.now(),
        nullable=False,
    )

    items: Mapped[List["OrderItem"]] = relationship(
        "OrderItem",
        back_populates="order",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __init__(self, **kwargs):
        if "status" not in kwargs or kwargs["status"] is None:
            kwargs["status"] = OrderStatus.DRAFT
        elif isinstance(kwargs["status"], str) and not isinstance(kwargs["status"], OrderStatus):
            kwargs["status"] = OrderStatus(kwargs["status"].lower())

        if "total_amount" not in kwargs or kwargs["total_amount"] is None:
            kwargs["total_amount"] = Decimal("0.00")
        else:
            kwargs["total_amount"] = _to_money(kwargs["total_amount"])

        super().__init__(**kwargs)

    @property
    def is_cart(self) -> bool:
        """Return True when the order is acting as a temporary shopping cart."""
        return self.status == OrderStatus.DRAFT

    def recalculate_total(self) -> Decimal:
        """Recalculate and update total_amount from all order items."""
        total = Decimal("0.00")
        for item in self.items or []:
            total += item.subtotal
        self.total_amount = _to_money(total)
        return self.total_amount

    def add_item(
        self,
        product_id: UUID,
        quantity: int,
        unit_price: Union[Decimal, float, int, str],
    ) -> "OrderItem":
        """Add or increment an item in a draft cart and recalculate the order total."""
        from app.models.orders.order_item import OrderItem

        if not self.is_cart:
            raise ValueError("Items can only be modified while the order is in draft (cart) status")
        if quantity <= 0:
            raise ValueError("Item quantity must be greater than zero")

        normalized_price = _to_money(unit_price)
        if normalized_price < Decimal("0.00"):
            raise ValueError("Item unit_price cannot be negative")

        for existing in self.items:
            if existing.product_id == product_id:
                existing.quantity += quantity
                existing.unit_price = normalized_price
                self.recalculate_total()
                return existing

        new_item = OrderItem(
            order_id=self.id,
            product_id=product_id,
            quantity=quantity,
            unit_price=normalized_price,
        )
        self.items.append(new_item)
        self.recalculate_total()
        return new_item

    def update_item_quantity(self, product_id: UUID, quantity: int) -> Optional["OrderItem"]:
        """Update the quantity of a product in the draft cart, removing it if quantity == 0."""
        if not self.is_cart:
            raise ValueError("Items can only be modified while the order is in draft (cart) status")
        if quantity < 0:
            raise ValueError("Item quantity cannot be negative")

        for existing in list(self.items):
            if existing.product_id == product_id:
                if quantity == 0:
                    self.items.remove(existing)
                    self.recalculate_total()
                    return None
                existing.quantity = quantity
                self.recalculate_total()
                return existing

        raise ValueError("Product not found in cart")

    def remove_item(self, product_id: UUID) -> bool:
        """Remove an item by product_id from the draft cart and recalculate total."""
        if not self.is_cart:
            raise ValueError("Items can only be modified while the order is in draft (cart) status")

        for existing in list(self.items):
            if existing.product_id == product_id:
                self.items.remove(existing)
                self.recalculate_total()
                return True
        return False

    def can_transition_to(self, next_status: Union[OrderStatus, str]) -> bool:
        """Check whether transitioning from current status to next_status is valid."""
        target = (
            next_status
            if isinstance(next_status, OrderStatus)
            else OrderStatus(str(next_status).lower())
        )
        return target in ALLOWED_ORDER_TRANSITIONS.get(self.status, set())

    def transition_to(self, next_status: Union[OrderStatus, str]) -> OrderStatus:
        """Transition order to next_status after enforcing business invariants."""
        target = (
            next_status
            if isinstance(next_status, OrderStatus)
            else OrderStatus(str(next_status).lower())
        )
        if not self.can_transition_to(target):
            raise ValueError(
                f"Invalid order status transition from '{self.status.value}' to '{target.value}'"
            )

        if self.status == OrderStatus.DRAFT and target == OrderStatus.PENDING:
            if not self.items:
                raise ValueError("Cannot checkout an empty draft cart without items")
            if not self.shipping_address or not self.shipping_address.strip():
                raise ValueError("Shipping address is required to submit a draft order")
            self.recalculate_total()

        self.status = target
        self.updated_at = datetime.utcnow()
        return self.status
