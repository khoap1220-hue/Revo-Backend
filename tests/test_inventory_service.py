"""
Unit tests for product inventory, seasonal availability detection, and availability filtering.
"""

from datetime import date
import uuid
import pytest

from app.models.products.inventory import AvailabilityStatus, ProductInventory
from app.schemas.product import ProductAvailabilityFilter, ProductInventoryCreate
from app.services.inventory_service import InventoryService


def test_product_displays_current_stock_correctly():
    inv = ProductInventory(
        product_id=uuid.uuid4(),
        product_name="Organic Hass Avocado",
        stock_quantity=50,
        reserved_quantity=12,
        is_seasonal=False,
    )
    status = InventoryService.sync_inventory_status(inv)
    assert inv.stock_quantity == 50
    assert inv.available_stock == 38
    assert status == AvailabilityStatus.AVAILABLE

    inv.stock_quantity = 12
    assert InventoryService.sync_inventory_status(inv) == AvailabilityStatus.OUT_OF_STOCK


def test_system_automatically_detects_if_product_is_in_season():
    season_start = date(2026, 5, 1)
    season_end = date(2026, 8, 31)

    # Before season starts -> coming_soon
    status_before = InventoryService.calculate_availability_status(
        stock_quantity=25,
        is_seasonal=True,
        season_start=season_start,
        season_end=season_end,
        reference_date=date(2026, 4, 10),
    )
    assert status_before == AvailabilityStatus.COMING_SOON

    # During season with positive stock -> available
    status_during = InventoryService.calculate_availability_status(
        stock_quantity=25,
        is_seasonal=True,
        season_start=season_start,
        season_end=season_end,
        reference_date=date(2026, 6, 15),
    )
    assert status_during == AvailabilityStatus.AVAILABLE

    # During season with zero stock -> out_of_stock
    status_empty = InventoryService.calculate_availability_status(
        stock_quantity=0,
        is_seasonal=True,
        season_start=season_start,
        season_end=season_end,
        reference_date=date(2026, 6, 15),
    )
    assert status_empty == AvailabilityStatus.OUT_OF_STOCK

    # After season ends -> out_of_season
    status_after = InventoryService.calculate_availability_status(
        stock_quantity=25,
        is_seasonal=True,
        season_start=season_start,
        season_end=season_end,
        reference_date=date(2026, 10, 1),
    )
    assert status_after == AvailabilityStatus.OUT_OF_SEASON


def test_availability_filters_work_in_product_queries():
    ref = date(2026, 6, 15)
    items = [
        ProductInventory(
            product_id=uuid.uuid4(),
            product_name="In-Season Strawberries",
            stock_quantity=40,
            reserved_quantity=0,
            is_seasonal=True,
            season_start=date(2026, 5, 1),
            season_end=date(2026, 7, 31),
        ),
        ProductInventory(
            product_id=uuid.uuid4(),
            product_name="Out-of-Stock Blueberries",
            stock_quantity=0,
            reserved_quantity=0,
            is_seasonal=False,
        ),
        ProductInventory(
            product_id=uuid.uuid4(),
            product_name="Autumn Pumpkins",
            stock_quantity=100,
            reserved_quantity=0,
            is_seasonal=True,
            season_start=date(2026, 9, 1),
            season_end=date(2026, 11, 30),
        ),
        ProductInventory(
            product_id=uuid.uuid4(),
            product_name="Spring Asparagus",
            stock_quantity=30,
            reserved_quantity=0,
            is_seasonal=True,
            season_start=date(2026, 3, 1),
            season_end=date(2026, 4, 30),
        ),
    ]

    available_only = InventoryService.filter_inventories(
        items,
        ProductAvailabilityFilter(status=AvailabilityStatus.AVAILABLE, reference_date=ref),
    )
    assert [i.product_name for i in available_only] == ["In-Season Strawberries"]

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
    with pytest.raises(ValueError):
        ProductInventoryCreate(
            product_id=uuid.uuid4(),
            stock_quantity=5,
            reserved_quantity=10,
        )
