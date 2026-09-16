#!/usr/bin/env bash
# Run the same hassfest the CI runs, before pushing.
#
# The strings files have a schema that only hassfest knows in full -- a
# selector's values must sit under "options", for one -- and a local test can
# only ever approximate it. Ten seconds here beats a red run.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
exec docker run --rm -v "$REPO":/github/workspace ghcr.io/home-assistant/hassfest
