"""
Inventory service for managing product stock and automatic seasonal availability calculation.
"""

from datetime import date
from typing import Iterable, List, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.products.inventory import (
    AvailabilityStatus,
    ProductInventory,
    SeasonalAvailability,
)
from app.schemas.product import (
    ProductAvailabilityFilter,
    ProductInventoryCreate,
    ProductInventoryUpdate,
)


class InventoryService:
    """Service class for product stock and seasonal availability operations."""

    @staticmethod
    def is_date_in_season(
        season_start: Optional[date],
        season_end: Optional[date],
        reference_date: Optional[date] = None,
    ) -> bool:
        """
        Check whether ``reference_date`` falls within ``[season_start, season_end]``.
        Supports both standard ranges within a single calendar year and wrap-around
        agricultural seasons (e.g. November to February).
        """
        if season_start is None or season_end is None:
            return True

        check_date = reference_date or date.today()
        if season_start <= season_end:
            return season_start <= check_date <= season_end

        # Wrap-around season across year boundary (by month/day)
        ref_md = (check_date.month, check_date.day)
        start_md = (season_start.month, season_start.day)
        end_md = (season_end.month, season_end.day)
        return ref_md >= start_md or ref_md <= end_md

    @classmethod
    def calculate_availability_status(
        cls,
        stock_quantity: int,
        reserved_quantity: int = 0,
        is_seasonal: bool = False,
        season_start: Optional[date] = None,
        season_end: Optional[date] = None,
        reference_date: Optional[date] = None,
    ) -> AvailabilityStatus:
        """
        Automatically calculate product availability status:
        - ``coming_soon``: Seasonal product whose start date is in the future (same year).
        - ``out_of_season``: Seasonal product outside its active harvest window.
        - ``out_of_stock``: Product is in season (or non-seasonal) but available stock <= 0.
        - ``available``: Product is in season (or non-seasonal) and has available stock > 0.
        """
        check_date = reference_date or date.today()
        effective_seasonal = is_seasonal or (
            season_start is not None and season_end is not None
        )

        if effective_seasonal and season_start is not None and season_end is not None:
            if not cls.is_date_in_season(season_start, season_end, check_date):
                if check_date < season_start:
                    return AvailabilityStatus.COMING_SOON
                return AvailabilityStatus.OUT_OF_SEASON

        available_stock = max(0, int(stock_quantity) - int(reserved_quantity))
        if available_stock <= 0:
            return AvailabilityStatus.OUT_OF_STOCK

        return AvailabilityStatus.AVAILABLE

    @classmethod
    def sync_inventory_status(
        cls,
        inventory: ProductInventory,
        reference_date: Optional[date] = None,
    ) -> AvailabilityStatus:
        """Recalculate and update the ``status`` attribute on a ``ProductInventory`` instance."""
        new_status = cls.calculate_availability_status(
            stock_quantity=inventory.stock_quantity or 0,
            reserved_quantity=inventory.reserved_quantity or 0,
            is_seasonal=bool(inventory.is_seasonal),
            season_start=inventory.season_start,
            season_end=inventory.season_end,
            reference_date=reference_date,
        )
        inventory.status = new_status
        return new_status

    @classmethod
    def filter_inventories(
        cls,
        inventories: Iterable[ProductInventory],
        criteria: ProductAvailabilityFilter,
    ) -> List[ProductInventory]:
        """Filter an iterable of inventory records by availability status, season, and minimum stock."""
        ref_date = criteria.reference_date or date.today()
        matched: List[ProductInventory] = []
        for inv in inventories:
            status = cls.sync_inventory_status(inv, reference_date=ref_date)
            if criteria.status is not None and status != criteria.status:
                continue
            if criteria.in_season_only and not cls.is_date_in_season(
                inv.season_start, inv.season_end, ref_date
            ):
                continue
            if criteria.min_stock is not None and inv.available_stock < criteria.min_stock:
                continue
            matched.append(inv)
        return matched

    @classmethod
    async def create(
        cls,
        db: AsyncSession,
        data: ProductInventoryCreate,
        reference_date: Optional[date] = None,
    ) -> ProductInventory:
        """Create a new product inventory record and compute its initial availability status."""
        payload = data.model_dump(exclude={"seasonal_windows"})
        inventory = ProductInventory(**payload)
        cls.sync_inventory_status(inventory, reference_date=reference_date)

        if data.seasonal_windows:
            inventory.seasonal_windows = [
                SeasonalAvailability(**win.model_dump())
                for win in data.seasonal_windows
            ]

        db.add(inventory)
        await db.commit()
        await db.refresh(inventory)
        return inventory

    @classmethod
    async def get_by_product_id(
        cls,
        db: AsyncSession,
        product_id: UUID,
        reference_date: Optional[date] = None,
    ) -> Optional[ProductInventory]:
        """Retrieve inventory for a product and refresh its availability status."""
        result = await db.execute(
            select(ProductInventory)
            .where(ProductInventory.product_id == product_id)
            .options(selectinload(ProductInventory.seasonal_windows))
        )
        inventory = result.scalar_one_or_none()
        if inventory is not None:
            cls.sync_inventory_status(inventory, reference_date=reference_date)
        return inventory

    @classmethod
    async def update(
        cls,
        db: AsyncSession,
        inventory: ProductInventory,
        data: ProductInventoryUpdate,
        reference_date: Optional[date] = None,
    ) -> ProductInventory:
        """Update stock or seasonal window fields and recalculate availability status."""
        for key, value in data.model_dump(exclude_unset=True).items():
            setattr(inventory, key, value)

        if (inventory.reserved_quantity or 0) > (inventory.stock_quantity or 0):
            raise ValueError("reserved_quantity cannot exceed stock_quantity")

        cls.sync_inventory_status(inventory, reference_date=reference_date)
        await db.commit()
        await db.refresh(inventory)
        return inventory

    @classmethod
    async def adjust_stock(
        cls,
        db: AsyncSession,
        inventory: ProductInventory,
        delta: int,
        reference_date: Optional[date] = None,
    ) -> ProductInventory:
        """Increment or decrement product stock safely and recalculate status."""
        new_quantity = (inventory.stock_quantity or 0) + delta
        if new_quantity < 0:
            raise ValueError("Stock quantity cannot become negative")
        if new_quantity < (inventory.reserved_quantity or 0):
            raise ValueError("Stock quantity cannot be less than reserved_quantity")

        inventory.stock_quantity = new_quantity
        cls.sync_inventory_status(inventory, reference_date=reference_date)
        await db.commit()
        await db.refresh(inventory)
        return inventory

    @classmethod
    async def list_by_availability(
        cls,
        db: AsyncSession,
        criteria: Optional[ProductAvailabilityFilter] = None,
    ) -> List[ProductInventory]:
        """Query all product inventories and apply availability and seasonal filters."""
        result = await db.execute(
            select(ProductInventory).options(
                selectinload(ProductInventory.seasonal_windows)
            )
        )
        items = list(result.scalars().all())
        if criteria is None:
            for inv in items:
                cls.sync_inventory_status(inv)
            return items
        return cls.filter_inventories(items, criteria)
