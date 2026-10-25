"""
Unit tests for product inventory, seasonal availability detection, and availability filtering.
"""

from datetime import date
from unittest.mock import AsyncMock, MagicMock
import uuid
import pytest

from app.models.products.inventory import (
    AvailabilityStatus,
    ProductInventory,
    SeasonalAvailability,
)
from app.schemas.product import (
    ProductAvailabilityFilter,
    ProductInventoryCreate,
    ProductInventoryResponse,
    ProductInventoryUpdate,
    SeasonalWindowCreate,
)
from app.services.inventory_service import InventoryService


def test_product_displays_current_stock_correctly():
    """Verify available_stock calculation, in_season property, and status synchronization."""
    inv = ProductInventory(
        id=uuid.uuid4(),
        product_id=uuid.uuid4(),
        product_name="Organic Hass Avocado",
        stock_quantity=50,
        reserved_quantity=12,
        low_stock_threshold=5,
        is_seasonal=False,
    )
    status = InventoryService.sync_inventory_status(inv)
    assert inv.stock_quantity == 50
    assert inv.available_stock == 38
    assert inv.in_season is True
    assert status == AvailabilityStatus.AVAILABLE

    response = ProductInventoryResponse.model_validate(inv)
    assert response.available_stock == 38
    assert response.in_season is True

    inv.stock_quantity = 12
    assert InventoryService.sync_inventory_status(inv) == AvailabilityStatus.OUT_OF_STOCK


def test_system_automatically_detects_if_product_is_in_season():
    """Verify coming_soon, available, out_of_stock, and out_of_season for same-year seasons."""
    season_start = date(2026, 5, 1)
    season_end = date(2026, 8, 31)

    status_before = InventoryService.calculate_availability_status(
        stock_quantity=25,
        is_seasonal=True,
        season_start=season_start,
        season_end=season_end,
        reference_date=date(2026, 4, 10),
    )
    assert status_before == AvailabilityStatus.COMING_SOON

    status_during = InventoryService.calculate_availability_status(
        stock_quantity=25,
        is_seasonal=True,
        season_start=season_start,
        season_end=season_end,
        reference_date=date(2026, 6, 15),
    )
    assert status_during == AvailabilityStatus.AVAILABLE

    status_empty = InventoryService.calculate_availability_status(
        stock_quantity=0,
        is_seasonal=True,
        season_start=season_start,
        season_end=season_end,
        reference_date=date(2026, 6, 15),
    )
    assert status_empty == AvailabilityStatus.OUT_OF_STOCK

    status_after = InventoryService.calculate_availability_status(
        stock_quantity=25,
        is_seasonal=True,
        season_start=season_start,
        season_end=season_end,
        reference_date=date(2026, 10, 1),
    )
    assert status_after == AvailabilityStatus.OUT_OF_SEASON


def test_wrap_around_annual_season_and_future_reference_years():
    """Verify November-to-February wrap-around season across 2026-2027 and later reference years."""
    season_start = date(2026, 11, 1)
    season_end = date(2027, 2, 28)

    assert InventoryService.is_date_in_season(
        season_start, season_end, date(2026, 12, 15)
    ) is True
    assert InventoryService.is_date_in_season(
        season_start, season_end, date(2028, 1, 20)
    ) is True
    assert InventoryService.is_date_in_season(
        season_start, season_end, date(2027, 10, 15)
    ) is False

    # Approaching next annual start in a later year -> COMING_SOON
    assert (
        InventoryService.calculate_availability_status(
            stock_quantity=20,
            is_seasonal=True,
            season_start=season_start,
            season_end=season_end,
            reference_date=date(2027, 10, 15),
        )
        == AvailabilityStatus.COMING_SOON
    )

    # Immediately after winter season ends -> OUT_OF_SEASON
    assert (
        InventoryService.calculate_availability_status(
            stock_quantity=20,
            is_seasonal=True,
            season_start=season_start,
            season_end=season_end,
            reference_date=date(2027, 3, 20),
        )
        == AvailabilityStatus.OUT_OF_SEASON
    )


def test_availability_filters_work_in_product_queries():
    """Verify status, in_season_only, min_stock, and seasonal_windows filtering without mutating ORM state."""
    ref = date(2026, 6, 15)
    window_item = ProductInventory(
        product_id=uuid.uuid4(),
        product_name="Windowed Summer Cherries",
        stock_quantity=15,
        reserved_quantity=3,
        is_seasonal=True,
        status=AvailabilityStatus.OUT_OF_STOCK,
    )
    window_item.seasonal_windows = [
        SeasonalAvailability(
            season_name="Early Summer",
            start_date=date(2026, 6, 1),
            end_date=date(2026, 6, 30),
        )
    ]

    items = [
        ProductInventory(
            product_id=uuid.uuid4(),
            product_name="In-Season Strawberries",
            stock_quantity=40,
            reserved_quantity=0,
            is_seasonal=True,
            season_start=date(2026, 5, 1),
            season_end=date(2026, 7, 31),
            status=AvailabilityStatus.OUT_OF_STOCK,
        ),
        ProductInventory(
            product_id=uuid.uuid4(),
            product_name="Out-of-Stock Blueberries",
            stock_quantity=0,
            reserved_quantity=0,
            is_seasonal=False,
            status=AvailabilityStatus.OUT_OF_STOCK,
        ),
        ProductInventory(
            product_id=uuid.uuid4(),
            product_name="Autumn Pumpkins",
            stock_quantity=100,
            reserved_quantity=0,
            is_seasonal=True,
            season_start=date(2026, 9, 1),
            season_end=date(2026, 11, 30),
            status=AvailabilityStatus.OUT_OF_STOCK,
        ),
        ProductInventory(
            product_id=uuid.uuid4(),
            product_name="Spring Asparagus",
            stock_quantity=30,
            reserved_quantity=0,
            is_seasonal=True,
            season_start=date(2026, 3, 1),
            season_end=date(2026, 4, 30),
            status=AvailabilityStatus.OUT_OF_STOCK,
        ),
        window_item,
    ]

    available_only = InventoryService.filter_inventories(
        items,
        ProductAvailabilityFilter(status=AvailabilityStatus.AVAILABLE, reference_date=ref),
    )
    assert [i.product_name for i in available_only] == [
        "In-Season Strawberries",
        "Windowed Summer Cherries",
    ]
    # Ensure read-path filtering did not mutate stored status on ORM instances
    assert items[0].status == AvailabilityStatus.OUT_OF_STOCK

    in_season_min_stock = InventoryService.filter_inventories(
        items,
        ProductAvailabilityFilter(in_season_only=True, min_stock=20, reference_date=ref),
    )
    assert [i.product_name for i in in_season_min_stock] == ["In-Season Strawberries"]

    coming_soon_only = InventoryService.filter_inventories(
        items,
        ProductAvailabilityFilter(status=AvailabilityStatus.COMING_SOON, reference_date=ref),
    )
    assert [i.product_name for i in coming_soon_only] == ["Autumn Pumpkins"]

    out_of_season_only = InventoryService.filter_inventories(
        items,
        ProductAvailabilityFilter(status=AvailabilityStatus.OUT_OF_SEASON, reference_date=ref),
    )
    assert [i.product_name for i in out_of_season_only] == ["Spring Asparagus"]


def test_schema_validation_rejects_invalid_reservation():
    """Verify ProductInventoryCreate rejects reserved_quantity > stock_quantity."""
    with pytest.raises(ValueError):
        ProductInventoryCreate(
            product_id=uuid.uuid4(),
            stock_quantity=5,
            reserved_quantity=10,
        )


@pytest.mark.asyncio
async def test_async_create_update_adjust_and_list_inventory():
    """Verify async create, update validation, adjust_stock row locking, and list_by_availability."""
    db = AsyncMock()
    db.add = MagicMock()

    create_data = ProductInventoryCreate(
        product_id=uuid.uuid4(),
        product_name="Winter Citrus",
        stock_quantity=30,
        reserved_quantity=5,
        is_seasonal=True,
        seasonal_windows=[
            SeasonalWindowCreate(
                season_name="Winter Harvest",
                start_date=date(2026, 11, 1),
                end_date=date(2027, 2, 28),
            )
        ],
    )
    created = await InventoryService.create(
        db, create_data, reference_date=date(2026, 7, 1)
    )
    assert created.status == AvailabilityStatus.OUT_OF_SEASON
    assert len(created.seasonal_windows) == 1

    # Reject invalid update before mutating ORM instance
    with pytest.raises(ValueError, match="reserved_quantity cannot exceed stock_quantity"):
        await InventoryService.update(
            db,
            created,
            ProductInventoryUpdate(reserved_quantity=50),
            reference_date=date(2026, 12, 1),
        )
    assert created.reserved_quantity == 5

    # Valid update during season transitions status to AVAILABLE
    updated = await InventoryService.update(
        db,
        created,
        ProductInventoryUpdate(stock_quantity=40),
        reference_date=date(2026, 12, 1),
    )
    assert updated.stock_quantity == 40
    assert updated.status == AvailabilityStatus.AVAILABLE

    # Adjust stock down to reserved_quantity -> OUT_OF_STOCK
    adjusted = await InventoryService.adjust_stock(
        db,
        updated,
        delta=-35,
        reference_date=date(2026, 12, 1),
    )
    assert adjusted.stock_quantity == 5
    assert adjusted.status == AvailabilityStatus.OUT_OF_STOCK

    # List by availability filter via mocked execute result
    scalars_mock = MagicMock()
    scalars_mock.all.return_value = [adjusted]
    execute_result = MagicMock()
    execute_result.scalars.return_value = scalars_mock
    db.execute.return_value = execute_result

    listed = await InventoryService.list_by_availability(
        db,
        ProductAvailabilityFilter(
            status=AvailabilityStatus.OUT_OF_STOCK,
            reference_date=date(2026, 12, 1),
        ),
    )
    assert len(listed) == 1
    assert listed[0].product_name == "Winter Citrus"
