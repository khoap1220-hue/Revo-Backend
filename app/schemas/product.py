"""
Pydantic schemas for product inventory and seasonal availability.
"""

from datetime import date, datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.models.products.inventory import AvailabilityStatus


class SeasonalWindowBase(BaseModel):
    season_name: Optional[str] = None
    start_date: date
    end_date: date


class SeasonalWindowCreate(SeasonalWindowBase):
    pass


class SeasonalWindowResponse(SeasonalWindowBase):
    id: UUID
    inventory_id: UUID

    class Config:
        from_attributes = True


class ProductInventoryBase(BaseModel):
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
        if self.reserved_quantity > self.stock_quantity:
            raise ValueError("reserved_quantity cannot exceed stock_quantity")
        if (self.season_start is None) ^ (self.season_end is None):
            raise ValueError("Both season_start and season_end must be provided together")
        return self


class ProductInventoryCreate(ProductInventoryBase):
    seasonal_windows: Optional[List[SeasonalWindowCreate]] = None


class ProductInventoryUpdate(BaseModel):
    product_name: Optional[str] = None
    stock_quantity: Optional[int] = Field(default=None, ge=0)
    reserved_quantity: Optional[int] = Field(default=None, ge=0)
    low_stock_threshold: Optional[int] = Field(default=None, ge=0)
    is_seasonal: Optional[bool] = None
    season_start: Optional[date] = None
    season_end: Optional[date] = None


class ProductInventoryResponse(ProductInventoryBase):
    id: UUID
    available_stock: int
    status: AvailabilityStatus
    in_season: bool
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class ProductAvailabilityFilter(BaseModel):
    status: Optional[AvailabilityStatus] = None
    in_season_only: bool = False
    min_stock: Optional[int] = Field(default=None, ge=0)
    reference_date: Optional[date] = None
