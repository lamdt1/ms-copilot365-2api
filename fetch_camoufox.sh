#!/bin/bash
set -e

echo "==> Fetching Camoufox browser binary..."
# camoufox fetch installs the browser successfully but fails on MMDB download — ignore that
python -m camoufox fetch || true

echo "==> Downloading GeoLite2 MMDB files from working mirror..."

# Find where camoufox expects its MMDB files
CAMOUFOX_DIR=$(python -c "import camoufox, os; print(os.path.dirname(camoufox.__file__))")
echo "    camoufox dir: $CAMOUFOX_DIR"

# Try common MMDB locations
for MMDB_DIR in "$CAMOUFOX_DIR/mmdb" "$HOME/.cache/camoufox/mmdb" "/root/.cache/camoufox/mmdb"; do
    mkdir -p "$MMDB_DIR"
    echo "    Downloading to $MMDB_DIR ..."
    curl -fsSL -o "$MMDB_DIR/geolite2-city-ipv4.mmdb" \
        "https://raw.githubusercontent.com/P3TERX/GeoLite.mmdb/download/GeoLite2-City.mmdb" || true
    curl -fsSL -o "$MMDB_DIR/geolite2-city-ipv6.mmdb" \
        "https://raw.githubusercontent.com/P3TERX/GeoLite.mmdb/download/GeoLite2-City.mmdb" || true
done

echo "==> Camoufox setup complete."
ls -lh "$CAMOUFOX_DIR/mmdb/" 2>/dev/null || true
ls -lh "/root/.cache/camoufox/mmdb/" 2>/dev/null || true
