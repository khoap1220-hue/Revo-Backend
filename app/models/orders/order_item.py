"""
OrderItem model representing line items within an Order or temporary shopping cart.
"""

from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import TYPE_CHECKING, Union
import uuid
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.orders.order import Order


def _to_money(value: Union[Decimal, float, int, str]) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class OrderItem(Base):
    """Line item belonging to an Order with automatic subtotal computation."""

    __tablename__ = "order_items"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    order_id: Mapped[UUID] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    product_id: Mapped[UUID] = mapped_column(
        nullable=False,
        index=True,
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
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

    order: Mapped["Order"] = relationship("Order", back_populates="items")

    def __init__(self, **kwargs):
        quantity = kwargs.get("quantity", 1)
        if quantity is None or int(quantity) <= 0:
            raise ValueError("OrderItem quantity must be greater than zero")
        kwargs["quantity"] = int(quantity)

        if "unit_price" not in kwargs or kwargs["unit_price"] is None:
            raise ValueError("OrderItem unit_price is required")
        normalized_price = _to_money(kwargs["unit_price"])
        if normalized_price < Decimal("0.00"):
            raise ValueError("OrderItem unit_price cannot be negative")
        kwargs["unit_price"] = normalized_price

        super().__init__(**kwargs)

    @property
    def subtotal(self) -> Decimal:
        """Calculate line item subtotal (quantity * unit_price) rounded to 2 decimal places."""
        return _to_money(Decimal(self.quantity) * _to_money(self.unit_price))
