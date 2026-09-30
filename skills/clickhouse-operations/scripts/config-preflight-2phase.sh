#!/usr/bin/env bash
# ClickHouse config.d change preflight (two-phase, read-only; never touches production containers/volumes)
#
# Why it has to be two-phase: under a brand-new data directory the system log tables are
# "lazily created", whereas a config semantics error (e.g. writing <ttl> for a table that
# already carries <engine> in the image -> Code 36) is only raised when metadata of an
# [already existing] table is loaded. Booting a single empty container = a false pass
# (observed in a real run on 2026-09-13).
#
# Usage: bash config-preflight-2phase.sh <image> <new-config.xml> [container_name]
#   bash config-preflight-2phase.sh clickhouse/clickhouse-server:26.7 ~/langfuse/clickhouse-system-ttl.xml
#
# PASS = all four assertions hold (missing any one = false pass):
#   ① the component answers a real request   ② the config is really loaded (logger.level=='warning')
#   ③ zero fatal errors in the log (by error code) ④ the new config applies to an [already existing] table (has_ttl==1)
set -u

IMG="${1:?usage: $0 <image> <new-config.xml> [name]}"
CFG="${2:?usage: $0 <image> <new-config.xml> [name]}"
PRE="${3:-ch-preflight}"
D="$HOME/.hermes/data/backups/preflight-datadir"     # temp data directory (removed when done)
OUT="$HOME/.hermes/data/backups/preflight-$(date +%Y%m%d_%H%M%S)"
mkdir -p "$OUT"

P() { docker exec "$PRE" clickhouse-client -n -q "$1" 2>&1; }

echo "===== two-phase preflight $(date +%F' '%T) output: $OUT ====="
echo "image=$IMG"; echo "config=$CFG"; sha256sum "$CFG"
rm -rf "$D"; mkdir -p "$D"; chmod 777 "$D"    # in-container uid is usually != the host user

# -- Phase A: no new config mounted; let the component create the tables first
echo "--- A. start the container (no config mounted) so the system log tables get created ---"
docker rm -f "$PRE" >/dev/null 2>&1
docker run --rm -d --name "$PRE" -m 1g -v "$D:/var/lib/clickhouse" "$IMG" >/dev/null \
  && echo "   started (waiting 75s)"
sleep 75
echo "   system log tables present:"
P "SELECT name FROM system.tables WHERE database='system' AND name IN ('text_log','trace_log','metric_log','part_log','query_log','background_schedule_pool_log','query_metric_log','error_log','opentelemetry_span_log') ORDER BY name"
docker rm -f "$PRE" >/dev/null 2>&1 && echo "   container A removed (data kept in $D)"

# -- Phase B: same data directory + new config mounted -> reproduces the production path
echo "--- B. same data directory + new config mounted, restart (reproduces the production path) ---"
docker run --rm -d --name "$PRE" -m 1g \
  -v "$D:/var/lib/clickhouse" \
  -v "$CFG:/etc/clickhouse-server/config.d/system-log-ttl.xml:ro" \
  "$IMG" >/dev/null && echo "   started (waiting 60s)"
sleep 60

echo "--- ① answers ---"; ANS=$(P "SELECT 'ALIVE'"); echo "   $ANS"
echo "--- ② config really loaded (must be warning) ---"
LVL=$(P "SELECT value FROM system.server_settings WHERE name='logger.level'"); echo "   logger.level=$LVL"
echo "--- ③ fatal errors in the log (Code 36 must be 0) ---"
docker cp "$PRE:/var/log/clickhouse-server/clickhouse-server.err.log" "$OUT/pre-err.log" >/dev/null 2>&1
# ⚠️ grep -c exits 1 on zero matches; swallow only the exit code, do NOT write `|| echo NA` (it would produce "0\nNA")
C36=$(grep -ac 'TTL parameters should be specified' "$OUT/pre-err.log" || true); C36=${C36:-NA}
echo "   Code 36 = $C36"
grep -a -E 'Caught exception|DB::Exception' "$OUT/pre-err.log" | head -5
echo "   (empty above = no other fatal errors)"
echo "--- ④ new config applied to an [already existing] table (must be 1) ---"
P "SELECT name, countSubstrings(create_table_query,'TTL event_date')>0 AS has_ttl FROM system.tables WHERE database='system' AND name IN ('text_log','background_schedule_pool_log','query_metric_log','error_log') ORDER BY name"
echo "--- container state (a crash loop would show up here as Up for a few seconds) ---"
docker ps -a --filter "name=$PRE" --format '{{.Names}}\t{{.Status}}'

docker rm -f "$PRE" >/dev/null 2>&1 && echo "   container B removed"
# Files are written inside the container with a non-host uid -> the host cannot delete them; clean up with a root container
docker run --rm -v "$D:/d" alpine sh -c 'rm -rf /d/* /d/.[!.]*' >/dev/null 2>&1 \
  && echo "   temp data directory cleaned" || echo "   ⚠️ temp directory $D not fully cleaned (needs root)"

echo; echo "===== verdict ====="
OK=yes
case "$ANS" in *ALIVE*) ;; *) OK=no; echo "✗ ① no response";; esac
[ "$C36" = "0" ] || { OK=no; echo "✗ ③ Code36=$C36"; }
[ "$LVL" = "warning" ] || { OK=no; echo "✗ ② logger.level=$LVL (config not in effect or the mount failed)"; }
if [ "$OK" = yes ]; then
  echo "✅ preflight passed (config confirmed loaded / semantically valid / applied to existing tables) -- safe to roll out to production"
else
  echo "❌ preflight failed -> do not touch production. Logs: $OUT"
fi
