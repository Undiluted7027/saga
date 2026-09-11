def process_order(order, account, payment_gateway):
    if not order.items:
        raise ValueError("order must contain items")
    assert account.active
    subtotal = sum(item.price for item in order.items)
    order.status = "ready"
    payment_gateway.charge(account, subtotal)
    return subtotal
