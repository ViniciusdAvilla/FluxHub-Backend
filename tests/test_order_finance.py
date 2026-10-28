from app.services.order_finance import summarize_order_finance


def _order():
    return {
        "external_id": "123", "seller_id": 1234, "status": "paid",
        "is_cancelled": False, "total_amount": 100,
        "items": [{"item_id": "MLB123", "title": "Produto", "quantity": 1,
                   "unit_price": 100, "sale_fee": 12, "unit_cost": 40,
                   "tax_percent": 7, "configured_shipping": 5, "other_cost": 2}],
    }


def test_actual_fee_breakdown_and_profit():
    value = summarize_order_finance(_order(), {})
    assert value["complete"] is True
    assert value["estimated_profit"] == 34
    assert value["estimated_margin"] == 34
    assert value["fees_are_estimated"] is False


def test_missing_fee_or_cost_never_reports_profit():
    order = _order()
    order["items"][0]["sale_fee"] = None
    assert summarize_order_finance(order, {})["estimated_profit"] is None
    estimate = summarize_order_finance(order, {"MLB123": 12})
    assert estimate["estimated_profit"] == 34
    assert estimate["fees_are_estimated"] is True
    order["items"][0]["unit_cost"] = None
    assert summarize_order_finance(order, {"MLB123": 12})["estimated_profit"] is None


def test_cancelled_is_not_realized_profit():
    order = _order()
    order["status"] = "cancelled"
    order["is_cancelled"] = True
    assert summarize_order_finance(order, {})["estimated_profit"] is None
