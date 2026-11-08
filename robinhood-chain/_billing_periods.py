import json
import sys
from pathlib import Path

sys.path.insert(0, "/root/data-download")
from _dune import DuneApi, resolve_api_key

api = DuneApi(resolve_api_key(None, Path("/root/data-download")))
periods = api.billing_periods()
print(json.dumps(periods, ensure_ascii=False, indent=2)[:4000])
