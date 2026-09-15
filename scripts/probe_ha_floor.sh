#!/usr/bin/env bash
# Find the oldest Home Assistant release this integration actually works on.
#
# Each candidate gets its own throwaway venv, because
# pytest-homeassistant-custom-component pins an exact homeassistant version.
# Usage: scripts/probe_ha_floor.sh 2025.4.4 2025.12.3 2026.2.3
set -uo pipefail

export PATH="$HOME/.local/bin:$PATH"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
WORK="${TMPDIR:-/tmp}/bl-floor-probe"
mkdir -p "$WORK"

for VERSION in "$@"; do
    VENV="$WORK/venv-$VERSION"
    echo "=============================================================="
    echo "Home Assistant $VERSION"
    echo "=============================================================="

    # Python floor moved at 2026.3, not at the year boundary: 2026.1 and
    # 2026.2 still run on 3.13, while 2026.3+ requires 3.14.2.
    case "$VERSION" in
        2026.1.*|2026.2.*) PY=3.13 ;;
        2026.*)            PY=3.14 ;;
        *)                 PY=3.13 ;;
    esac

    rm -rf "$VENV"
    if ! uv venv --python "$PY" "$VENV" >/dev/null 2>&1; then
        echo "RESULT $VERSION: SKIP (no Python $PY available)"
        continue
    fi

    if ! VIRTUAL_ENV="$VENV" uv pip install -q \
            "homeassistant==$VERSION" pytest-homeassistant-custom-component \
            >"$WORK/install-$VERSION.log" 2>&1; then
        echo "RESULT $VERSION: SKIP (dependency resolution failed)"
        tail -3 "$WORK/install-$VERSION.log" | sed 's/^/    /'
        continue
    fi

    if (cd "$REPO" && "$VENV/bin/python" -m pytest tests/ -q \
            >"$WORK/pytest-$VERSION.log" 2>&1); then
        echo "RESULT $VERSION: PASS"
    else
        echo "RESULT $VERSION: FAIL"
        grep -E "^(FAILED|ERROR)|short test summary" "$WORK/pytest-$VERSION.log" \
            | head -8 | sed 's/^/    /'
    fi
    rm -rf "$VENV"
done

echo
echo "Logs in $WORK"
