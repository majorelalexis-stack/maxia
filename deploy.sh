#!/bin/bash
set -e

echo '=== MAXIA Deploy ==='

cd /opt/maxia
sudo git pull origin main
git log --oneline -1

sudo systemctl restart maxia
echo 'Restarted, waiting 6s...'
sleep 6

echo '=== Smoke Test ==='
FAIL=0

CODE=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/)
[ "$CODE" = '200' ] && echo "OK: site 200" || { echo "FAIL: site $CODE"; FAIL=1; }

CODE=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/api/public/forum)
[ "$CODE" = '200' ] && echo "OK: forum 200" || { echo "FAIL: forum $CODE"; FAIL=1; }

PRICES=$(curl -s http://127.0.0.1:8000/api/public/crypto/prices)
CG=$(echo "$PRICES" | python3 -c 'import sys,json; d=json.load(sys.stdin); p=d.get("prices",d); print(sum(1 for v in p.values() if isinstance(v,dict) and v.get("source")=="coingecko"))' 2>/dev/null || echo 0)
FB=$(echo "$PRICES" | python3 -c 'import sys,json; d=json.load(sys.stdin); p=d.get("prices",d); print(sum(1 for v in p.values() if isinstance(v,dict) and v.get("source")=="fallback"))' 2>/dev/null || echo 0)
[ "$CG" -gt 0 ] && echo "OK: prices $CG live, $FB fallback" || echo "WARN: prices 0 live, $FB fallback"

STATS=$(curl -s http://127.0.0.1:8000/api/public/stats)
SVCS=$(echo "$STATS" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("services_listed",0))' 2>/dev/null || echo 0)
echo "OK: stats $SVCS services"

TOOLS=$(curl -s http://127.0.0.1:8000/mcp/manifest | python3 -c 'import sys,json; print(len(json.load(sys.stdin).get("tools",[])))' 2>/dev/null || echo 0)
[ "$TOOLS" -gt 40 ] && echo "OK: MCP $TOOLS tools" || { echo "FAIL: MCP $TOOLS tools"; FAIL=1; }

SQ=$(curl -s 'http://127.0.0.1:8000/api/public/crypto/quote?from_token=SOL&to_token=USDC&amount=1' | python3 -c 'import sys,json; d=json.load(sys.stdin); print("ok" if d.get("output_amount",0)>0 else "fail")' 2>/dev/null || echo fail)
[ "$SQ" = 'ok' ] && echo "OK: swap quote" || echo "WARN: swap quote $SQ"

ERRS=$(sudo journalctl -u maxia --no-pager -n 50 --since '30 sec ago' | grep -ci 'error\|traceback\|nameerror' || true)
[ "$ERRS" -gt 0 ] && echo "WARN: $ERRS errors in logs" || echo "OK: 0 errors in logs"

echo ''
[ $FAIL -eq 0 ] && echo '=== DEPLOY OK ===' || { echo '=== DEPLOY FAILED ==='; exit 1; }
