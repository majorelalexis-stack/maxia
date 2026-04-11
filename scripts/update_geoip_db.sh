#!/usr/bin/env bash
# update_geoip_db.sh — Download/refresh MaxMind GeoLite2-Country.mmdb
#
# Usage:
#   MAXMIND_LICENSE_KEY=xxx ./scripts/update_geoip_db.sh
#
# Cron recommended: monthly, first Tuesday at 03:00
#   0 3 1-7 * 2 /opt/maxia/scripts/update_geoip_db.sh >> /var/log/maxia-geoip.log 2>&1
#
# Output location: backend/core/data/GeoLite2-Country.mmdb
#
# Requirements:
#   - Free MaxMind account (https://www.maxmind.com/en/geolite2/signup)
#   - Export license key as MAXMIND_LICENSE_KEY env var
#   - curl + tar + sha256sum on the host

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DATA_DIR="${REPO_ROOT}/backend/core/data"
TARGET="${DATA_DIR}/GeoLite2-Country.mmdb"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "${TMP_DIR}"' EXIT

if [[ -z "${MAXMIND_LICENSE_KEY:-}" ]]; then
    echo "ERROR: MAXMIND_LICENSE_KEY env var not set."
    echo ""
    echo "Get a free license key at:"
    echo "  https://www.maxmind.com/en/accounts/current/license-key"
    echo ""
    echo "Then run:"
    echo "  export MAXMIND_LICENSE_KEY='your-key-here'"
    echo "  ./scripts/update_geoip_db.sh"
    exit 1
fi

mkdir -p "${DATA_DIR}"

URL="https://download.maxmind.com/app/geoip_download?edition_id=GeoLite2-Country&license_key=${MAXMIND_LICENSE_KEY}&suffix=tar.gz"
SHA_URL="https://download.maxmind.com/app/geoip_download?edition_id=GeoLite2-Country&license_key=${MAXMIND_LICENSE_KEY}&suffix=tar.gz.sha256"

echo "[geoip] downloading GeoLite2-Country.tar.gz..."
curl -fsSL "${URL}" -o "${TMP_DIR}/GeoLite2-Country.tar.gz"

echo "[geoip] downloading SHA-256 checksum..."
curl -fsSL "${SHA_URL}" -o "${TMP_DIR}/GeoLite2-Country.tar.gz.sha256"

echo "[geoip] verifying checksum..."
cd "${TMP_DIR}"
# MaxMind checksum files are formatted "<hash>  <filename>"
EXPECTED_SHA=$(awk '{print $1}' GeoLite2-Country.tar.gz.sha256)
ACTUAL_SHA=$(sha256sum GeoLite2-Country.tar.gz | awk '{print $1}')
if [[ "${EXPECTED_SHA}" != "${ACTUAL_SHA}" ]]; then
    echo "ERROR: SHA-256 mismatch — refusing to install"
    echo "  expected: ${EXPECTED_SHA}"
    echo "  actual:   ${ACTUAL_SHA}"
    exit 2
fi
echo "[geoip] checksum OK"

echo "[geoip] extracting..."
tar -xzf GeoLite2-Country.tar.gz
MMDB_FILE=$(find . -name "GeoLite2-Country.mmdb" -type f | head -1)
if [[ -z "${MMDB_FILE}" ]]; then
    echo "ERROR: GeoLite2-Country.mmdb not found in archive"
    exit 3
fi

echo "[geoip] installing to ${TARGET}"
cp "${MMDB_FILE}" "${TARGET}.new"
mv "${TARGET}.new" "${TARGET}"
chmod 644 "${TARGET}"

SIZE=$(stat -c '%s' "${TARGET}" 2>/dev/null || stat -f '%z' "${TARGET}")
echo "[geoip] done — ${TARGET} (${SIZE} bytes)"
echo ""
echo "Next steps:"
echo "  1. Restart the MAXIA backend to load the new DB:"
echo "     sudo systemctl restart maxia"
echo "  2. Verify the middleware picked it up:"
echo "     curl -H 'X-Admin-Key: \$ADMIN_KEY' https://maxiaworld.app/api/admin/geofence/status"
echo "     (look for 'maxmind_db_present: true')"
