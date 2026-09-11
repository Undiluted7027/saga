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
