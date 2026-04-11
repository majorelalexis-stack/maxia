# backend/core/data/

This directory holds binary data files that are not source code:

- `GeoLite2-Country.mmdb` — MaxMind GeoLite2 Country database used by
  the geofence middleware. NOT committed to git (license forbids
  redistribution).

## Installing GeoLite2

1. Create a free MaxMind account: https://www.maxmind.com/en/geolite2/signup
2. Get a license key: https://www.maxmind.com/en/accounts/current/license-key
3. Export it:
   ```bash
   export MAXMIND_LICENSE_KEY='your-key-here'
   ```
4. Run the update script from the repo root:
   ```bash
   ./scripts/update_geoip_db.sh
   ```

The script downloads, verifies SHA-256, and installs to
`backend/core/data/GeoLite2-Country.mmdb` (~70 MB).

## Automatic refresh

Add to crontab for monthly refresh (first Tuesday, 03:00):

```
0 3 1-7 * 2 MAXMIND_LICENSE_KEY=your-key /opt/maxia/scripts/update_geoip_db.sh >> /var/log/maxia-geoip.log 2>&1
```

## Fallback

If the MMDB file is missing, the geofence middleware falls back to
`ip-api.com` HTTP lookups (rate-limited to 40/min, introduces 100-300ms
latency per unknown IP). Local MaxMind is strongly recommended for prod.
