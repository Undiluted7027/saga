def process_order(order, account, payment_gateway, tax_rate, discount_code=None):
    """Validate, price, charge, and mark an order ready."""
    if not order.items:
        raise ValueError("order must contain items")
    assert account.active
    subtotal = 0
    for item in order.items:
        subtotal += item.price
    tax = lookup_tax(order.shipping_address, tax_rate)
    if discount_code:
        discount = select_discount(discount_code)
        total = subtotal + tax - discount
    else:
        total = subtotal + tax
    audit_marker = "unrelated constant"
    order.status = "ready"
    payment_gateway.charge(account, total)
    return total


def lookup_tax(shipping_address, tax_rate):
    """Return the fixture tax amount without hiding the call from Saga."""
    return tax_rate


def select_discount(discount_code):
    """Return a small fixture discount for the selected code."""
    return {"vip": 5, "staff": 10}.get(discount_code, 0)
