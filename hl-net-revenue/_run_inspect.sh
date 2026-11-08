#!/usr/bin/env bash
set -euo pipefail
docker cp /tmp/_inspect_manifest.js dashboard:/tmp/_inspect_manifest.js
docker exec dashboard node /tmp/_inspect_manifest.js
