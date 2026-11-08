#!/usr/bin/env bash
set -euo pipefail
KEY=/root/.ssh/data-dashboard-prod
HOST=root@47.74.6.207
SRC=/mnt/e/Code/data-download/spacex-venue-prices
DD=/mnt/e/Code/data-download

ssh -i "$KEY" -o IdentitiesOnly=yes -o ConnectTimeout=20 "$HOST" \
  'mkdir -p /root/data-download/spacex-venue-prices/output/json /root/dashboard/public/json/spacex-venue-prices'

scp -i "$KEY" -o IdentitiesOnly=yes \
  "$SRC/flash.py" \
  "$HOST:/root/data-download/spacex-venue-prices/"
scp -i "$KEY" -o IdentitiesOnly=yes \
  "$DD/spot-break-liq/run_monitor.sh" \
  "$HOST:/root/data-download/spot-break-liq/"

ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  "python3 -c \"from pathlib import Path
for p in ['/root/data-download/spacex-venue-prices/flash.py','/root/data-download/spot-break-liq/run_monitor.sh']:
 t=Path(p).read_bytes().replace(b'\\r\\n', b'\\n').replace(b'\\r', b'\\n'); Path(p).write_bytes(t)\""

ssh -i "$KEY" -o IdentitiesOnly=yes "$HOST" \
  'cd /root/data-download && .venv/bin/python spacex-venue-prices/flash.py --push && mkdir -p /root/dashboard/public/json/spacex-venue-prices && cp -a spacex-venue-prices/output/json/spacex_venue_prices.json /root/dashboard/public/json/spacex-venue-prices/spacex_venue_prices.json && python3 -c "import json;d=json.load(open(\"spacex-venue-prices/output/json/spacex_venue_prices.json\"));print(\"venues\",len(d.get(\"venues\",[])),\"spread\",d.get(\"spread_pct\"),\"extras\",len(d.get(\"extras\",[])))"'

echo DEPLOY_SPACEX_VENUE_PRICES_OK
