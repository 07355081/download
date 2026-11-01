#!/bin/bash
# OKX spot break + CoinGlass global liquidations → Lark.
# Suggested crontab (every 5 minutes):
# */5 * * * * /usr/local/bin/safe-run /root/data-download/spot-break-liq/run_monitor.sh
set -euo pipefail
cd /root/data-download
LOG="${LOG:-/var/log/dashboard-spot-break-liq.log}"
{
  echo "[$(date '+%F %T')] start"
  ./.venv/bin/python spot-break-liq/monitor.py --push
  mkdir -p /root/dashboard/public/json/spot-break-liq
  cp -f spot-break-liq/output/json/latest.json /root/dashboard/public/json/spot-break-liq/latest.json \
    || echo "[$(date '+%F %T')] spot-break-liq json warn"
  ./.venv/bin/python hype-spot/monitor.py --push \
    || echo "[$(date '+%F %T')] hype-spot warn"
  mkdir -p /root/dashboard/public/json/hype-spot
  cp -a hype-spot/output/json/hype_spot.json /root/dashboard/public/json/hype-spot/hype_spot.json \
    || echo "[$(date '+%F %T')] hype-spot json warn"
  ./.venv/bin/python spacex-perp/monitor.py --push \
    || echo "[$(date '+%F %T')] spacex-perp warn"
  mkdir -p /root/dashboard/public/json/spacex-perp
  cp -a spacex-perp/output/json/spacex_perp.json /root/dashboard/public/json/spacex-perp/spacex_perp.json \
    || echo "[$(date '+%F %T')] spacex-perp json warn"
  ./.venv/bin/python bnb-spot/monitor.py --push \
    || echo "[$(date '+%F %T')] bnb-spot warn"
  mkdir -p /root/dashboard/public/json/bnb-spot
  cp -a bnb-spot/output/json/bnb_spot.json /root/dashboard/public/json/bnb-spot/bnb_spot.json \
    || echo "[$(date '+%F %T')] bnb-spot json warn"
  ./.venv/bin/python bianrensheng-spot/monitor.py --push \
    || echo "[$(date '+%F %T')] bianrensheng-spot warn"
  mkdir -p /root/dashboard/public/json/bianrensheng-spot
  cp -a bianrensheng-spot/output/json/bianrensheng_spot.json \
    /root/dashboard/public/json/bianrensheng-spot/bianrensheng_spot.json \
    || echo "[$(date '+%F %T')] bianrensheng-spot json warn"
  ./.venv/bin/python spacex-liquidity/monitor.py --push \
    || echo "[$(date '+%F %T')] spacex-liquidity warn"
  mkdir -p /root/dashboard/public/json/spacex-liquidity
  cp -a spacex-liquidity/output/json/spacex_liquidity.json /root/dashboard/public/json/spacex-liquidity/spacex_liquidity.json \
    || echo "[$(date '+%F %T')] spacex-liquidity json warn"
  ./.venv/bin/python spacex-venue-prices/flash.py --push \
    || echo "[$(date '+%F %T')] spacex-venue-prices warn"
  mkdir -p /root/dashboard/public/json/spacex-venue-prices
  cp -a spacex-venue-prices/output/json/spacex_venue_prices.json \
    /root/dashboard/public/json/spacex-venue-prices/spacex_venue_prices.json \
    || echo "[$(date '+%F %T')] spacex-venue-prices json warn"
  ./.venv/bin/python pyth-status/monitor.py --push \
    || echo "[$(date '+%F %T')] pyth-status warn"
  mkdir -p /root/dashboard/public/json/pyth-status
  cp -a pyth-status/output/json/pyth_status.json /root/dashboard/public/json/pyth-status/pyth_status.json \
    || echo "[$(date '+%F %T')] pyth-status json warn"
  ./.venv/bin/python polymarket-status/monitor.py --push \
    || echo "[$(date '+%F %T')] polymarket-status warn"
  mkdir -p /root/dashboard/public/json/polymarket-status
  cp -a polymarket-status/output/json/polymarket_status.json \
    /root/dashboard/public/json/polymarket-status/polymarket_status.json \
    || echo "[$(date '+%F %T')] polymarket-status json warn"
  ./.venv/bin/python sui-status/monitor.py --push \
    || echo "[$(date '+%F %T')] sui-status warn"
  mkdir -p /root/dashboard/public/json/sui-status
  cp -a sui-status/output/json/sui_status.json /root/dashboard/public/json/sui-status/sui_status.json \
    || echo "[$(date '+%F %T')] sui-status json warn"
  echo "[$(date '+%F %T')] done"
} >>"$LOG" 2>&1
