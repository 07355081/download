"""Built-in Glassnode metric catalog (paths from docs.glassnode.com).

Curated set: BTC 13 + ETH 11 = 24 metrics.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MetricSpec:
    rank: int
    name: str
    path: str
    asset: str = "BTC"
    params: dict[str, str] | None = None


def _spec(rank: int, name: str, path: str, asset: str = "BTC", **extra: str) -> MetricSpec:
    params = {"a": asset, "i": "24h", **extra}
    return MetricSpec(rank=rank, name=name, path=path, asset=asset, params=params)


METRIC_SPECS: list[MetricSpec] = [
    _spec(1, 'MVRV Z Score', '/v1/metrics/market/mvrv_z_score'),
    _spec(4, 'Realized Price', '/v1/metrics/market/price_realized_usd'),
    _spec(6, 'Balanced Price', '/v1/metrics/indicators/balanced_price_usd'),
    _spec(8, 'CVDD', '/v1/metrics/indicators/cvdd'),
    _spec(9, 'Entity Adjusted NUPL', '/v1/metrics/indicators/net_unrealized_profit_loss_account_based'),
    _spec(14, 'Long Term Holder Position Change', '/v1/metrics/supply/lth_net_change'),
    _spec(16, 'HODL Waves', '/v1/metrics/supply/hodl_waves'),
    _spec(17, 'Seller Exhaustion Constant', '/v1/metrics/indicators/seller_exhaustion_constant'),
    _spec(18, 'Market Cap to Thermocap Ratio', '/v1/metrics/mining/marketcap_thermocap_ratio'),
    _spec(33, 'aSOPR', '/v1/metrics/indicators/sopr_adjusted'),
    _spec(58, 'Entity Adjusted 90D Coin Days Destroyed eCDD 90', '/v1/metrics/indicators/cdd90_account_based_age_adjusted'),
    _spec(84, 'Hash Ribbon', '/v1/metrics/indicators/hash_ribbon'),
    _spec(95, 'Reserve Risk', '/v1/metrics/indicators/reserve_risk'),
    _spec(201, 'ETH MVRV Z-Score', '/v1/metrics/market/mvrv_z_score', asset="ETH"),
    _spec(204, 'ETH NUPL', '/v1/metrics/indicators/net_unrealized_profit_loss', asset="ETH"),
    _spec(205, 'ETH Realized Price', '/v1/metrics/market/price_realized_usd', asset="ETH"),
    _spec(211, 'ETH SOPR', '/v1/metrics/indicators/sopr', asset="ETH"),
    _spec(236, 'ETH Transfer Volume', '/v1/metrics/transactions/transfers_volume_sum', asset="ETH"),
    _spec(239, 'ETH Stablecoin Transfer Share', '/v1/metrics/transactions/transfers_count_stablecoins_relative', asset="ETH"),
    _spec(241, 'ETH Fees Total', '/v1/metrics/fees/volume_sum', asset="ETH"),
    _spec(248, 'ETH Fee Ratio Multiple', '/v1/metrics/fees/fee_ratio_multiple', asset="ETH"),
    _spec(249, 'ETH Burn Rate', '/v1/metrics/supply/burn_rate', asset="ETH"),
    _spec(253, 'ETH Total Value Staked', '/v1/metrics/eth2/staking_total_volume_sum', asset="ETH"),
    _spec(255, 'ETH Validator ROI', '/v1/metrics/eth2/estimated_annual_issuance_roi_per_validator', asset="ETH"),
]

# BTC 14: miners + supply + holder cost + cycle valuation
# ETH 11: revenue + usage + staking + core valuation

