"""Generate glassnode_metrics_catalog.py (curated value metrics only)."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "glassnode_metrics_catalog.py"

# (rank, display_name, api_path)
BTC_METRICS: list[tuple[int, str, str]] = [
    (84, "Hash Ribbon", "/v1/metrics/indicators/hash_ribbon"),
    (18, "Market Cap to Thermocap Ratio", "/v1/metrics/mining/marketcap_thermocap_ratio"),
    (16, "HODL Waves", "/v1/metrics/supply/hodl_waves"),
    (14, "Long Term Holder Position Change", "/v1/metrics/supply/lth_net_change"),
    (58, "Entity Adjusted 90D Coin Days Destroyed eCDD 90", "/v1/metrics/indicators/cdd90_account_based_age_adjusted"),
    (4, "Realized Price", "/v1/metrics/market/price_realized_usd"),
    (6, "Balanced Price", "/v1/metrics/indicators/balanced_price_usd"),
    (8, "CVDD", "/v1/metrics/indicators/cvdd"),
    (17, "Seller Exhaustion Constant", "/v1/metrics/indicators/seller_exhaustion_constant"),
    (1, "MVRV Z Score", "/v1/metrics/market/mvrv_z_score"),
    (9, "Entity Adjusted NUPL", "/v1/metrics/indicators/net_unrealized_profit_loss_account_based"),
    (33, "aSOPR", "/v1/metrics/indicators/sopr_adjusted"),
    (95, "Reserve Risk", "/v1/metrics/indicators/reserve_risk"),
]

ETH_METRICS: list[tuple[int, str, str]] = [
    (241, "ETH Fees Total", "/v1/metrics/fees/volume_sum"),
    (248, "ETH Fee Ratio Multiple", "/v1/metrics/fees/fee_ratio_multiple"),
    (249, "ETH Burn Rate", "/v1/metrics/supply/burn_rate"),
    (236, "ETH Transfer Volume", "/v1/metrics/transactions/transfers_volume_sum"),
    (239, "ETH Stablecoin Transfer Share", "/v1/metrics/transactions/transfers_count_stablecoins_relative"),
    (253, "ETH Total Value Staked", "/v1/metrics/eth2/staking_total_volume_sum"),
    (255, "ETH Validator ROI", "/v1/metrics/eth2/estimated_annual_issuance_roi_per_validator"),
    (201, "ETH MVRV Z-Score", "/v1/metrics/market/mvrv_z_score"),
    (204, "ETH NUPL", "/v1/metrics/indicators/net_unrealized_profit_loss"),
    (205, "ETH Realized Price", "/v1/metrics/market/price_realized_usd"),
    (211, "ETH SOPR", "/v1/metrics/indicators/sopr"),
]


def main() -> None:
    lines = [
        '"""Built-in Glassnode metric catalog (paths from docs.glassnode.com).',
        "",
        "Curated set: BTC 13 + ETH 11 = 24 metrics.",
        '"""',
        "from __future__ import annotations",
        "",
        "from dataclasses import dataclass",
        "",
        "",
        "@dataclass(frozen=True)",
        "class MetricSpec:",
        "    rank: int",
        "    name: str",
        "    path: str",
        "    asset: str = \"BTC\"",
        "    params: dict[str, str] | None = None",
        "",
        "",
        "def _spec(rank: int, name: str, path: str, asset: str = \"BTC\", **extra: str) -> MetricSpec:",
        "    params = {\"a\": asset, \"i\": \"24h\", **extra}",
        "    return MetricSpec(rank=rank, name=name, path=path, asset=asset, params=params)",
        "",
        "",
        "METRIC_SPECS: list[MetricSpec] = [",
    ]

    for rank, name, path in sorted(BTC_METRICS, key=lambda x: x[0]):
        lines.append(f"    _spec({rank}, {name!r}, {path!r}),")

    for rank, name, path in sorted(ETH_METRICS, key=lambda x: x[0]):
        lines.append(f"    _spec({rank}, {name!r}, {path!r}, asset=\"ETH\"),")

    lines.extend(
        [
            "]",
            "",
            "# BTC 14: miners + supply + holder cost + cycle valuation",
            "# ETH 12: revenue + usage + staking + core valuation",
            "",
        ]
    )

    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")
    print(f"  BTC: {len(BTC_METRICS)}  ETH: {len(ETH_METRICS)}  total: {len(BTC_METRICS) + len(ETH_METRICS)}")


if __name__ == "__main__":
    main()
