"""
Pydantic schemas for product inventory and seasonal availability.
"""

from datetime import date, datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.products.inventory import AvailabilityStatus


class SeasonalWindowBase(BaseModel):
    """Base attributes for a seasonal harvest or availability window."""

    season_name: Optional[str] = None
    start_date: date
    end_date: date


class SeasonalWindowCreate(SeasonalWindowBase):
    """Schema for creating a seasonal availability window."""

    pass


class SeasonalWindowResponse(SeasonalWindowBase):
    """Response schema for a persisted seasonal availability window."""

    id: UUID
    inventory_id: UUID

    model_config = ConfigDict(from_attributes=True)


class ProductInventoryBase(BaseModel):
    """Base schema for product stock and seasonal configuration."""

    product_id: UUID
    product_name: Optional[str] = None
    stock_quantity: int = Field(default=0, ge=0)
    reserved_quantity: int = Field(default=0, ge=0)
    low_stock_threshold: int = Field(default=5, ge=0)
    is_seasonal: bool = False
    season_start: Optional[date] = None
    season_end: Optional[date] = None

    @model_validator(mode="after")
    def validate_quantities_and_dates(self) -> "ProductInventoryBase":
        """Validate that reserved stock does not exceed total stock and season dates are paired."""
        if self.reserved_quantity > self.stock_quantity:
            raise ValueError("reserved_quantity cannot exceed stock_quantity")
        if (self.season_start is None) ^ (self.season_end is None):
            raise ValueError("Both season_start and season_end must be provided together")
        return self


class ProductInventoryCreate(ProductInventoryBase):
    """Schema for creating a product inventory record with optional seasonal windows."""

    seasonal_windows: Optional[List[SeasonalWindowCreate]] = None


class ProductInventoryUpdate(BaseModel):
    """Schema for updating mutable product stock or seasonal configuration fields."""

    product_name: Optional[str] = None
    stock_quantity: Optional[int] = Field(default=None, ge=0)
    reserved_quantity: Optional[int] = Field(default=None, ge=0)
    low_stock_threshold: Optional[int] = Field(default=None, ge=0)
    is_seasonal: Optional[bool] = None
    season_start: Optional[date] = None
    season_end: Optional[date] = None


class ProductInventoryResponse(ProductInventoryBase):
    """Response schema representing a product inventory record and computed availability."""

    id: UUID
    available_stock: int
    status: AvailabilityStatus
    in_season: bool = True
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class ProductAvailabilityFilter(BaseModel):
    """Query filter criteria for listing products by status, seasonality, and stock."""

    status: Optional[AvailabilityStatus] = None
    in_season_only: bool = False
    min_stock: Optional[int] = Field(default=None, ge=0)
    reference_date: Optional[date] = None
