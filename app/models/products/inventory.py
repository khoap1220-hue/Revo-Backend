"""
Product inventory and seasonal availability models.
"""

from datetime import date, datetime
from enum import Enum
import uuid
from typing import Optional
from uuid import UUID

from sqlalchemy import Boolean, Date, DateTime, Enum as SqlEnum, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class AvailabilityStatus(str, Enum):
    """Availability status options for marketplace products."""

    AVAILABLE = "available"
    OUT_OF_STOCK = "out_of_stock"
    COMING_SOON = "coming_soon"
    OUT_OF_SEASON = "out_of_season"


class ProductInventory(Base):
    """Tracks stock levels and seasonal availability windows for a product."""

    __tablename__ = "product_inventories"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    product_id: Mapped[UUID] = mapped_column(unique=True, index=True, nullable=False)
    product_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    stock_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reserved_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    low_stock_threshold: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    is_seasonal: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    season_start: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    season_end: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    status: Mapped[AvailabilityStatus] = mapped_column(
        SqlEnum(AvailabilityStatus, name="availability_status_enum"),
        nullable=False,
        default=AvailabilityStatus.OUT_OF_STOCK,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), onupdate=func.now()
    )

    seasonal_windows: Mapped[list["SeasonalAvailability"]] = relationship(
        "SeasonalAvailability",
        back_populates="inventory",
        cascade="all, delete-orphan",
    )

    @property
    def available_stock(self) -> int:
        """Return unreserved stock available for purchase."""
        return max(0, (self.stock_quantity or 0) - (self.reserved_quantity or 0))

    @property
    def in_season(self) -> bool:
        """Return whether the product is currently in season on today's date."""
        today = date.today()
        if self.season_start is not None and self.season_end is not None:
            ref_md = (today.month, today.day)
            start_md = (self.season_start.month, self.season_start.day)
            end_md = (self.season_end.month, self.season_end.day)
            if start_md <= end_md:
                return start_md <= ref_md <= end_md
            return ref_md >= start_md or ref_md <= end_md
        windows = self.__dict__.get("seasonal_windows") or []
        if windows:
            ref_md = (today.month, today.day)
            for win in windows:
                start_md = (win.start_date.month, win.start_date.day)
                end_md = (win.end_date.month, win.end_date.day)
                if start_md <= end_md and (start_md <= ref_md <= end_md):
                    return True
                if start_md > end_md and (ref_md >= start_md or ref_md <= end_md):
                    return True
            return False
        return True


class SeasonalAvailability(Base):
    """Represents a seasonal harvest/availability window for an inventory item."""

    __tablename__ = "seasonal_availabilities"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    inventory_id: Mapped[UUID] = mapped_column(
        ForeignKey("product_inventories.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    season_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)

    inventory: Mapped["ProductInventory"] = relationship(
        "ProductInventory", back_populates="seasonal_windows"
    )
