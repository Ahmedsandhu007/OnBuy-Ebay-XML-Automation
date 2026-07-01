"""Selling price calculation.

The previous formula was `max(cost*(1+min_profit%), cost*(1+markup%))` with no
platform fee anywhere, even though a fee is always deducted by OnBuy on the
sale. This grosses the price up so the configured minimum profit is protected
*after* the fee is taken out, instead of the fee silently eating into it.
"""

MIN_PROFIT_PERCENT = 15
DEFAULT_MARKUP_PERCENT = 40
PLATFORM_FEE_PERCENT = 9  # set to your actual OnBuy commission rate for the category


def calculate_selling_price(
    cost_price,
    shipping_cost=0.0,
    *,
    min_profit_percent=MIN_PROFIT_PERCENT,
    default_markup_percent=DEFAULT_MARKUP_PERCENT,
    platform_fee_percent=PLATFORM_FEE_PERCENT,
):
    """Returns the selling price such that, after OnBuy's percentage fee is
    deducted, at least `min_profit_percent` margin over (cost + shipping)
    remains. shipping_cost defaults to 0 since the current sheet has no
    shipping column yet - wire it up by passing row.get("Shipping Cost (£)").
    """
    if cost_price <= 0:
        return 0.0

    total_cost = cost_price + shipping_cost
    min_price_before_fee = total_cost * (1 + min_profit_percent / 100)
    markup_price_before_fee = total_cost * (1 + default_markup_percent / 100)
    base_price = max(min_price_before_fee, markup_price_before_fee)

    fee_multiplier = 1 - (platform_fee_percent / 100)
    if fee_multiplier <= 0:
        raise ValueError("platform_fee_percent must be less than 100")

    return round(base_price / fee_multiplier, 2)
