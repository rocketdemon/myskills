#!/usr/bin/env bash
# Full, non-truncated sweep for copies of one credential value.
# Prints file NAMES and COUNTS only — never a matching line.
#
# Usage: sweep-secret-copies.sh <config-key-path> [root]
#   <config-key-path>  dotted path into config.yaml, e.g. mcp_servers.<server>.env.APP_SECRET
#   [root]             directory to sweep (default: $HOME/.hermes)
#
# Why this exists: the per-category breakdown must never be piped through `head`,
# and the category counts must be asserted against the file total. A truncated
# breakdown looks complete while dropping the biggest category (measured: 96
# reported vs 270 real — the session transcript stores sorted below the cut).
set -uo pipefail

KEY="${1:?usage: sweep-secret-copies.sh <config-key-path> [root]}"
ROOT="${2:-$HOME/.hermes}"
CFG="${HERMES_CONFIG:-$HOME/.hermes/config.yaml}"
LIST="${TMPDIR:-/tmp}/secret-copies-$$.txt"

# Read the value from config inside the script: never take a secret as an argv argument.
VALUE=""
for PY in python3 "$HOME/.hermes/hermes-agent/.venv/bin/python"; do
  [ -x "$PY" ] || command -v "$PY" >/dev/null 2>&1 || continue
  VALUE="$(KEY="$KEY" CFG="$CFG" "$PY" - <<'PY' 2>/dev/null
import os, sys
try:
    import yaml
except Exception:
    sys.exit(9)
key = os.environ["KEY"].split(".")
try:
    d = yaml.safe_load(open(os.environ["CFG"]))
except Exception:
    sys.exit(9)
for part in key:
    if not isinstance(d, dict) or part not in d:
        sys.exit(f"key not found: {os.environ['KEY']}")
    d = d[part]
print(d if isinstance(d, str) else "", end="")
PY
)" && [ -n "$VALUE" ] && break
done

[ -n "$VALUE" ] || { echo "cannot read a value for config key $KEY (the value IS the secret — never pass it as an argv argument)"; exit 3; }
echo "sweep root  : $ROOT"
echo "value length: ${#VALUE} (never echoed)"

grep -rlF --binary-files=text "$VALUE" "$ROOT" \
  --exclude-dir=.venv --exclude-dir=node_modules --exclude-dir=_npx --exclude-dir=.git \
  2>/dev/null | sed "s#^$ROOT/##" | sort > "$LIST"

TOTAL=$(wc -l < "$LIST")
echo
echo "matching files: $TOTAL"
echo "-- bucketed by the first two path levels (complete, never piped through head) --"
awk -F/ '{if (NF==1) print "<root>"; else if (NF==2 && $2 ~ /\./) print "<root file>"; else print $1"/"$2}' "$LIST" \
  | sort | uniq -c | sort -rn
echo
BUCKET_SUM=$(awk -F/ '{print $1}' "$LIST" | wc -l)
if [ "$BUCKET_SUM" = "$TOTAL" ]; then
  echo "reconcile OK: top-level bucket sum $BUCKET_SUM == file total $TOTAL"
else
  echo "reconcile FAIL: top-level bucket sum $BUCKET_SUM != file total $TOTAL — fix the script before quoting any number"
fi
echo
echo "full list (file names, one per line): $LIST"
echo "reminder: the contents of these files hold that credential — do not copy the list or their contents off this machine; rotation is the only way to make the copies worthless."
