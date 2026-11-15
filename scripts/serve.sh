#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH="$PWD/backend${PYTHONPATH:+:$PYTHONPATH}"
exec "${TSN_PYTHON:-python3}" -m designer.api
