"""Best-effort, explicit seller order breakdown; financial status is never fabricated.

Actual settled funds, sales tax regimes, refunds and corporate taxes require
additional sources. Missing cost/fee yields an unknown margin, not zero profit.
"""
from __future__ import annotations

from math import isfinite
from typing import Any


def _money(value: float) -> float:
    return round(value + 1e-10, 2)


def _valid_money(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return parsed if isfinite(parsed) and 0 <= parsed <= 1_000_000_000 else None


def summarize_order_finance(
    order: dict[str, Any],
    marketplace_rates: dict[str, float | None],
) -> dict[str, Any]:
    """Returns a per-order, per-line estimate with explicit data provenance."""
    result_items = []
    gross_sum = 0.0
    fee_sum = 0.0
    cost_sum = 0.0
    tax_sum = 0.0
    shipping_sum = 0.0
    other_sum = 0.0
    complete = bool(order.get("items"))
    estimated_fee = False
    for item in order.get("items", []):
        quantity = int(item.get("quantity") or 0)
        price = _valid_money(item.get("unit_price"))
        if price is None or quantity <= 0:
            complete = False
            continue
        gross = _money(price * quantity)
        fee = _valid_money(item.get("sale_fee"))
        fee_source = "mercado_livre_order" if fee is not None else "not_available"
        if fee is None:
            rate = _valid_money(marketplace_rates.get(str(item.get("item_id") or "")))
            if rate is not None and rate <= 100:
                fee = _money(gross * rate / 100)
                fee_source = "configured_percentage_estimate"
                estimated_fee = True
        unit_cost = _valid_money(item.get("unit_cost"))
        tax_percent = _valid_money(item.get("tax_percent"))
        shipping = _valid_money(item.get("configured_shipping"))
        other = _valid_money(item.get("other_cost"))
        product_cost = _money(unit_cost * quantity) if unit_cost is not None else None
        tax = _money(gross * tax_percent / 100) if tax_percent is not None else None
        has_cost_data = (product_cost is not None and tax is not None and
                         shipping is not None and other is not None)
        measurable = fee is not None and has_cost_data
        profit = _money(gross - fee - product_cost - tax - shipping - other) if measurable else None
        if not measurable:
            complete = False
        gross_sum += gross
        if fee is not None:
            fee_sum += fee
        if product_cost is not None:
            cost_sum += product_cost
        if tax is not None:
            tax_sum += tax
        if shipping is not None:
            shipping_sum += shipping
        if other is not None:
            other_sum += other
        result_items.append({
            "item_id": item.get("item_id"), "title": item.get("title"),
            "quantity": quantity, "gross": gross,
            "fee": fee, "fee_source": fee_source,
            "product_cost": product_cost, "taxes_configured": tax,
            "shipping_configured": shipping, "other_costs": other,
            "estimated_profit": profit,
            "estimated_margin": _money(100 * profit / gross) if profit is not None and gross else None,
        })

    is_paid = order.get("status") == "paid" and not order.get("is_cancelled")
    # A cancelled/unpaid order is excluded from realized profitability.
    estimated_profit = _money(gross_sum - fee_sum - cost_sum - tax_sum - shipping_sum - other_sum) if complete and is_paid else None
    return {
        "order_id": order.get("external_id"),
        "seller_id": order.get("seller_id"),
        "status": order.get("status"),
        "gross_order_amount": _valid_money(order.get("total_amount")),
        "gross_item_amount": _money(gross_sum),
        "marketplace_fees": _money(fee_sum) if complete else None,
        "product_cost": _money(cost_sum) if complete else None,
        "taxes_configured": _money(tax_sum) if complete else None,
        "shipping_configured": _money(shipping_sum) if complete else None,
        "other_costs": _money(other_sum) if complete else None,
        "estimated_profit": estimated_profit,
        "estimated_margin": _money(100 * estimated_profit / gross_sum)
            if estimated_profit is not None and gross_sum else None,
        "complete": complete and is_paid,
        "fees_are_estimated": estimated_fee,
        "items": result_items,
        "note": ("Projeção baseada nas taxas do pedido ou taxas configuradas, custos e tributos "
                 "cadastrados. Não equivale a saldo liquidado, lucro fiscal ou devoluções "
                 "conciliadas. Pedidos não pagos/cancelados não geram lucro realizado."),
    }
