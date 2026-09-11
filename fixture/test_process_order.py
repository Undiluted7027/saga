"""Pytest executions used by Saga's observed-evidence fixture."""

from types import SimpleNamespace

from fixture.process_order import process_order


class Gateway:
    """Record a charge without performing an external operation."""

    def __init__(self):
        self.charges = []

    def charge(self, account, amount):
        """Record the amount charged by the fixture target."""
        self.charges.append((account, amount))


def order_with(*prices):
    """Build the bounded object shape used by the fixture tests."""
    return SimpleNamespace(items=[SimpleNamespace(price=price) for price in prices], shipping_address="CA")


def account():
    """Build an active fixture account."""
    return SimpleNamespace(active=True)


def test_process_order_basic():
    """Exercise one ordinary order."""
    result = process_order(order_with(10), account(), Gateway(), 2)
    assert result == 12


def test_process_order_discounted():
    """Exercise subtotal accumulation and the discount branch."""
    result = process_order(order_with(30, 20), account(), Gateway(), 5, "vip")
    assert result == 50


def test_process_order_repeated_input():
    """Repeat an existing input so it cannot inflate distinct support."""
    result = process_order(order_with(10), account(), Gateway(), 2)
    assert result == 12
