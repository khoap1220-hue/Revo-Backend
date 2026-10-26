"""
Inventory service for managing product stock and seasonal availability calculation.
"""

from datetime import date
from typing import Iterable, List, Optional, Sequence
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
        Compares ``(month, day)`` consistently for both same-year ranges and
        wrap-around ranges across the calendar year boundary.
        """
        if season_start is None or season_end is None:
            return True

        check_date = reference_date or date.today()
        ref_md = (check_date.month, check_date.day)
        start_md = (season_start.month, season_start.day)
        end_md = (season_end.month, season_end.day)

        if start_md <= end_md:
            return start_md <= ref_md <= end_md

        # Wrap-around season across year boundary (e.g. Nov 1 to Feb 28)
        return ref_md >= start_md or ref_md <= end_md

    @classmethod
    def _is_before_next_annual_start(
        cls,
        season_start: date,
        season_end: date,
        check_date: date,
    ) -> bool:
        """
        Determine whether an out-of-season ``check_date`` should be classified as
        ``COMING_SOON`` (approaching the next annual season start) rather than
        ``OUT_OF_SEASON`` (after the season has ended).
        """
        ref_md = (check_date.month, check_date.day)
        start_md = (season_start.month, season_start.day)
        end_md = (season_end.month, season_end.day)

        if start_md <= end_md:
            return ref_md < start_md

        # Wrap-around range (start_md > end_md, e.g. Nov 1 to Feb 28):
        # Out-of-season dates lie strictly between end_md and start_md.
        if check_date < season_start:
            return True
        midpoint_month = (end_md[0] + start_md[0]) / 2.0
        return ref_md[0] >= midpoint_month

    @classmethod
    def is_inventory_in_season(
        cls,
        inventory: ProductInventory,
        reference_date: Optional[date] = None,
    ) -> bool:
        """
        Evaluate whether ``inventory`` is in season on ``reference_date`` using
        top-level ``season_start``/``season_end`` or ``inventory.seasonal_windows``.
        """
        check_date = reference_date or date.today()
        if inventory.season_start is not None and inventory.season_end is not None:
            return cls.is_date_in_season(
                inventory.season_start, inventory.season_end, check_date
            )
        windows: Sequence[SeasonalAvailability] = (
            inventory.__dict__.get("seasonal_windows") or []
        )
        if windows:
            return any(
                cls.is_date_in_season(win.start_date, win.end_date, check_date)
                for win in windows
            )
        return True

    @classmethod
    def calculate_availability_status(
        cls,
        stock_quantity: int,
        reserved_quantity: int = 0,
        is_seasonal: bool = False,
        season_start: Optional[date] = None,
        season_end: Optional[date] = None,
        reference_date: Optional[date] = None,
        seasonal_windows: Optional[Sequence[SeasonalAvailability]] = None,
    ) -> AvailabilityStatus:
        """
        Automatically calculate product availability status:
        - ``coming_soon``: Seasonal product prior to its upcoming annual start date.
        - ``out_of_season``: Seasonal product outside its active harvest window.
        - ``out_of_stock``: Product is in season (or non-seasonal) with stock <= 0.
        - ``available``: Product is in season (or non-seasonal) with stock > 0.
        """
        check_date = reference_date or date.today()
        windows = list(seasonal_windows) if seasonal_windows else []
        effective_seasonal = (
            is_seasonal
            or (season_start is not None and season_end is not None)
            or bool(windows)
        )

        if effective_seasonal:
            if season_start is not None and season_end is not None:
                if not cls.is_date_in_season(season_start, season_end, check_date):
                    if cls._is_before_next_annual_start(
                        season_start, season_end, check_date
                    ):
                        return AvailabilityStatus.COMING_SOON
                    return AvailabilityStatus.OUT_OF_SEASON
            elif windows:
                if not any(
                    cls.is_date_in_season(w.start_date, w.end_date, check_date)
                    for w in windows
                ):
                    if any(
                        cls._is_before_next_annual_start(
                            w.start_date, w.end_date, check_date
                        )
                        for w in windows
                    ):
                        return AvailabilityStatus.COMING_SOON
                    return AvailabilityStatus.OUT_OF_SEASON

        available_stock = max(0, int(stock_quantity) - int(reserved_quantity))
        if available_stock <= 0:
            return AvailabilityStatus.OUT_OF_STOCK

        return AvailabilityStatus.AVAILABLE

    @classmethod
    def get_inventory_status(
        cls,
        inventory: ProductInventory,
        reference_date: Optional[date] = None,
    ) -> AvailabilityStatus:
        """Compute availability status without mutating tracked ORM state."""
        windows = inventory.__dict__.get("seasonal_windows") or []
        return cls.calculate_availability_status(
            stock_quantity=inventory.stock_quantity or 0,
            reserved_quantity=inventory.reserved_quantity or 0,
            is_seasonal=bool(inventory.is_seasonal),
            season_start=inventory.season_start,
            season_end=inventory.season_end,
            reference_date=reference_date,
            seasonal_windows=windows,
        )

    @classmethod
    def sync_inventory_status(
        cls,
        inventory: ProductInventory,
        reference_date: Optional[date] = None,
    ) -> AvailabilityStatus:
        """Recalculate and update the ``status`` attribute on ``inventory``."""
        new_status = cls.get_inventory_status(inventory, reference_date=reference_date)
        inventory.status = new_status
        return new_status

    @classmethod
    def filter_inventories(
        cls,
        inventories: Iterable[ProductInventory],
        criteria: ProductAvailabilityFilter,
    ) -> List[ProductInventory]:
        """Filter inventory records by availability status, season, and stock."""
        ref_date = criteria.reference_date or date.today()
        matched: List[ProductInventory] = []
        for inv in inventories:
            status = cls.get_inventory_status(inv, reference_date=ref_date)
            if criteria.status is not None and status != criteria.status:
                continue
            if criteria.in_season_only and not cls.is_inventory_in_season(
                inv, reference_date=ref_date
            ):
                continue
            if (
                criteria.min_stock is not None
                and inv.available_stock < criteria.min_stock
            ):
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
        """Create a product inventory record and compute its initial status."""
        payload = data.model_dump(exclude={"seasonal_windows"})
        inventory = ProductInventory(**payload)

        if data.seasonal_windows:
            inventory.seasonal_windows = [
                SeasonalAvailability(**win.model_dump())
                for win in data.seasonal_windows
            ]

        cls.sync_inventory_status(inventory, reference_date=reference_date)

        db.add(inventory)
        await db.commit()
        inventory = await cls._reload_inventory(db, inventory)
        return inventory

    @classmethod
    async def get_by_product_id(
        cls,
        db: AsyncSession,
        product_id: UUID,
        reference_date: Optional[date] = None,
    ) -> Optional[ProductInventory]:
        """Retrieve inventory for a product without dirtying session state."""
        result = await db.execute(
            select(ProductInventory)
            .where(ProductInventory.product_id == product_id)
            .options(selectinload(ProductInventory.seasonal_windows))
        )
        return result.scalar_one_or_none()

    @classmethod
    async def _reload_inventory(
        cls,
        db: AsyncSession,
        inventory: ProductInventory,
        lock: bool = False,
    ) -> ProductInventory:
        """Reload an inventory instance with seasonal_windows eagerly populated."""
        if getattr(inventory, "id", None) is not None and hasattr(db, "execute"):
            stmt = (
                select(ProductInventory)
                .options(selectinload(ProductInventory.seasonal_windows))
                .where(ProductInventory.id == inventory.id)
                .execution_options(populate_existing=True)
            )
            if lock:
                stmt = stmt.with_for_update()
            result = await db.execute(stmt)
            reloaded = result.scalar_one_or_none()
            if reloaded is not None:
                return reloaded
        if lock:
            inventory = await cls._reload_inventory(db, inventory, lock=True)
        else:
            await db.refresh(inventory)
        return inventory

    @classmethod
    async def update(
        cls,
        db: AsyncSession,
        inventory: ProductInventory,
        data: ProductInventoryUpdate,
        reference_date: Optional[date] = None,
    ) -> ProductInventory:
        """Update stock or seasonal window fields safely and recalculate status."""
        inventory = await cls._reload_inventory(db, inventory, lock=True)

        updates = data.model_dump(exclude_unset=True)
        non_nullable = {
            "stock_quantity",
            "reserved_quantity",
            "low_stock_threshold",
            "is_seasonal",
        }
        for key in non_nullable.intersection(updates):
            if updates[key] is None:
                raise ValueError(f"{key} cannot be null")

        stock = updates.get("stock_quantity", inventory.stock_quantity)
        reserved = updates.get("reserved_quantity", inventory.reserved_quantity)
        season_start = updates.get("season_start", inventory.season_start)
        season_end = updates.get("season_end", inventory.season_end)

        if (reserved or 0) > (stock or 0):
            raise ValueError("reserved_quantity cannot exceed stock_quantity")
        if (season_start is None) ^ (season_end is None):
            raise ValueError(
                "Both season_start and season_end must be provided together"
            )

        for key, value in updates.items():
            setattr(inventory, key, value)

        cls.sync_inventory_status(inventory, reference_date=reference_date)
        await db.commit()
        inventory = await cls._reload_inventory(db, inventory)
        return inventory

    @classmethod
    async def adjust_stock(
        cls,
        db: AsyncSession,
        inventory: ProductInventory,
        delta: int,
        reference_date: Optional[date] = None,
    ) -> ProductInventory:
        """Increment or decrement product stock with row locking and sync status."""
        inventory = await cls._reload_inventory(db, inventory, lock=True)
        new_quantity = (inventory.stock_quantity or 0) + delta
        if new_quantity < 0:
            raise ValueError("Stock quantity cannot become negative")
        if new_quantity < (inventory.reserved_quantity or 0):
            raise ValueError("Stock quantity cannot be less than reserved_quantity")

        inventory.stock_quantity = new_quantity
        cls.sync_inventory_status(inventory, reference_date=reference_date)
        await db.commit()
        inventory = await cls._reload_inventory(db, inventory)
        return inventory

    @classmethod
    async def list_by_availability(
        cls,
        db: AsyncSession,
        criteria: Optional[ProductAvailabilityFilter] = None,
    ) -> List[ProductInventory]:
        """Query product inventories and apply availability filters."""
        result = await db.execute(
            select(ProductInventory).options(
                selectinload(ProductInventory.seasonal_windows)
            )
        )
        items = list(result.scalars().all())
        if criteria is None:
            return items
        return cls.filter_inventories(items, criteria)
