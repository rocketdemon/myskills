#!/bin/bash
# Download any SPA's entry bundle and all lazy-loaded chunks (for static reconnaissance, read-only).
#
# Usage: bash fetch-spa-chunks.sh <base-url> [outdir]
#   bash fetch-spa-chunks.sh https://example.com/ /tmp/example_chunks
#
# Self-contained flow: fetch the home page HTML → take the first <script src> as the entry → download the entry
#            → extract the `assets/*.js` list from the entry → download them one by one into outdir.
# Artifacts: _index.html / _entry.js / _chunks.txt / each <hash>.js
set -u

BASE="${1:?Usage: fetch-spa-chunks.sh <base-url> [outdir]}"
OUT="${2:-/tmp/spa_chunks}"
case "$BASE" in */) ;; *) BASE="$BASE/" ;; esac
mkdir -p "$OUT"

curl -s --max-time 30 "$BASE" -o "$OUT/_index.html"
ENTRY=$(grep -o 'src="[^"]*\.js"' "$OUT/_index.html" | head -1 | sed 's/^src="//; s/"$//')
if [ -z "$ENTRY" ]; then
  echo "No JS entry found on the home page (it may be plain SSR/MPA, or the home page was blocked) —— first do UA spoofing per SKILL.md §2"
  exit 1
fi
case "$ENTRY" in
  http*) EURL="$ENTRY" ;;
  /*)    EURL="$BASE${ENTRY#/}" ;;
  *)     EURL="$BASE$ENTRY" ;;
esac
echo "Entry: $EURL"
curl -s --max-time 60 "$EURL" -o "$OUT/_entry.js"
echo "Entry size: $(stat -c %s "$OUT/_entry.js" 2>/dev/null || echo 0) bytes"

grep -o "assets/[A-Za-z0-9_.-]*\.js" "$OUT/_entry.js" | sort -u > "$OUT/_chunks.txt"
echo "Lazy-loaded chunk count: $(wc -l < "$OUT/_chunks.txt")"
while read -r f; do
  [ -z "$f" ] && continue
  curl -s --max-time 60 "$BASE$f" -o "$OUT/$(basename "$f")"
done < "$OUT/_chunks.txt"

echo "Done: $(ls "$OUT" | wc -l) files, total $(du -sh "$OUT" | cut -f1)"
echo "Next step: python3 analyze-spa-bundle.py $OUT"
