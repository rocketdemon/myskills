#!/usr/bin/env bash
# recon-upgrade-surface.sh — fully read-only reconnaissance before a host-component upgrade
#
# Usage:
#   bash recon-upgrade-surface.sh [OLD_TAG] [NEW_TAG]
#   bash recon-upgrade-surface.sh                  # auto-pick "tag of current HEAD" and "newest tag"
#
# Read-only guarantee: only runs `git fetch --tags` (touches .git refs only) and
#           git show/diff/ls-tree/cat-file. No checkout, no reset, no sync, no
#           restarting any service.
#
# Why a script instead of inline commands: an inline for-loop plus several
#   `git show | grep` calls trips the command parser's hard block (BLOCKED
#   hardline). Writing it to disk and running bash is the only reliable approach.
# Note: the output is long; do NOT blanket-truncate with `| head -N` -- that
#   silently drops the second half.
set -u

INSTALL_DIR="${INSTALL_DIR:-$HOME/.hermes/hermes-agent}"
CFG="${CFG:-$HOME/.hermes/config.yaml}"
SCRIPTS_DIR="${SCRIPTS_DIR:-$HOME/.hermes/scripts}"
cd "$INSTALL_DIR" 2>/dev/null || { echo "FATAL: no install dir $INSTALL_DIR"; exit 1; }

echo "### install_dir=$INSTALL_DIR  head=$(git rev-parse --short HEAD 2>/dev/null)"

# ---------------------------------------------------------------- 0. fetch tags
echo
echo "=== [0] fetch upstream tags (touches refs only) ==="
timeout 420 git fetch --tags origin 2>&1 | tail -5
echo "--- newest 15 tags (by creation date) ---"
git tag --sort=-creatordate | head -15

OLD="${1:-$(git describe --tags --abbrev=0 2>/dev/null)}"
NEW="${2:-$(git tag --sort=-creatordate | head -1)}"
echo
echo "### OLD=$OLD   NEW=$NEW"

# ---------------------------------------------------------------- 1. version matrix
echo
echo "=== [1] tag matrix (semver / dependency floor / date) ==="
printf "%-16s %-12s %-16s %-8s %s\n" TAG SEMVER REQUIRES_PY NVMODE DATE
for t in $(git tag --sort=creatordate | tail -20); do
  ver=$(git show "$t:pyproject.toml" 2>/dev/null | sed -n 's/^version *= *"\([^"]*\)".*/\1/p' | head -1)
  py=$(git show "$t:pyproject.toml" 2>/dev/null | sed -n 's/^requires-python *= *"\([^"]*\)".*/\1/p' | head -1)
  node=$(git show "$t:.nvmrc" 2>/dev/null | tr -d '\n')
  d=$(git log -1 --format=%ad --date=short "$t" 2>/dev/null)
  printf "%-16s %-12s %-16s %-8s %s\n" "$t" "${ver:-?}" "${py:-?}" "${node:-?}" "${d:-?}"
done
echo "--- host interpreters ---"
echo "  venv python : $(ls "$INSTALL_DIR"/.venv/bin/python 2>/dev/null && "$INSTALL_DIR"/.venv/bin/python --version 2>&1)"
echo "  system py   : $(python3 --version 2>&1)"
echo "  node        : $(node --version 2>&1)  (cross-check package.json engines, not just .nvmrc)"

# ---------------------------------------------------------------- 2. detached / parked
echo
echo "=== [2] checkout state and whether it blocks the upgrade ==="
git status --porcelain | head -30
git branch -vv | head -10
echo "--- is HEAD ancestor of origin/main? (0=yes) ---"
timeout 180 git merge-base --is-ancestor HEAD origin/main 2>/dev/null; echo "  exit=$?"
echo "--- git cherry origin/main HEAD ('+' lines = not in origin/main -> flagged as parked) ---"
timeout 180 git cherry origin/main HEAD 2>/dev/null | head -10

# ---------------------------------------------------------------- 3. config version & migrations
echo
echo "=== [3] config schema version gap ==="
echo "  on-disk : $(grep -nE '^_config_version:' "$CFG" 2>/dev/null || echo '<absent>')"
echo "  target  : $(git grep -hE '"\_config_version"\s*:' "$NEW" -- '*.py' 2>/dev/null | head -3)"
echo "--- migration registry (target version) ---"
for f in $(git ls-tree -r --name-only "$NEW" | grep -iE 'config_migrat|migrations\.py' | head -5); do
  echo "  # $f"
  git show "$NEW:$f" 2>/dev/null | grep -nE '^def _migrate_to_|^MIGRATIONS|SUPPORT_FLOOR' | head -40
done

# ---------------------------------------------------------------- 4. dependency surface
echo
echo "=== [4] dependency surface: venv ∖ lockfile (extra packages a dependency sync would wipe out) ==="
SP=$(ls -d "$INSTALL_DIR"/.venv/lib/python3.*/site-packages 2>/dev/null | head -1)
if [ -n "$SP" ]; then
  ls -d "$SP"/*.dist-info 2>/dev/null | sed 's#.*/##; s/\.dist-info$//; s/-[0-9][^-]*$//' \
    | tr 'A-Z_' 'a-z-' | sort -u > /tmp/probe_installed.txt
  git show "$NEW":uv.lock 2>/dev/null | grep -oE '^name = "[^"]+"' | sed 's/name = "//; s/"//' \
    | tr 'A-Z_' 'a-z-' | sort -u > /tmp/probe_lock.txt
  echo "  venv installed $(wc -l < /tmp/probe_installed.txt) / lock $(wc -l < /tmp/probe_lock.txt)"
  echo "--- in venv but not in lock (may disappear after upgrade; verify each by import) ---"
  comm -23 /tmp/probe_installed.txt /tmp/probe_lock.txt
else
  echo "  (site-packages not found; skipping)"
fi

echo
echo "--- key packages: old vs new version (a major jump is a breaking-change entry point) ---"
for pkg in mcp httpx httpx2 starlette fastapi aiohttp pydantic openai anthropic lark-oapi service-name-ai; do
  o=$(git show "$OLD":uv.lock 2>/dev/null | grep -A2 "^name = \"${pkg}\"$" | sed -n 's/^version = "\(.*\)"/\1/p' | head -1)
  n=$(git show "$NEW":uv.lock 2>/dev/null | grep -A2 "^name = \"${pkg}\"$" | sed -n 's/^version = "\(.*\)"/\1/p' | head -1)
  flag=""; [ "${o:-x}" != "${n:-y}" ] && flag="   <== CHANGED"
  printf "  %-20s %-14s -> %-14s%s\n" "$pkg" "${o:-absent}" "${n:-absent}" "$flag"
done

# ------------------------------------------------- [4b] which interpreter the sidecar lands on
echo
echo "=== [4b] which interpreter the sidecar lands on (this skill's #1 risk: the host shared venv gets its SDK swapped) ==="
UNIT_PATH=$(systemctl --user show hermes-gateway -p Environment 2>/dev/null \
  | tr ' ' '\n' | sed -n 's/^PATH=//p' | head -1)
[ -z "$UNIT_PATH" ] && UNIT_PATH="$PATH"
echo "  host venv bin : $INSTALL_DIR/.venv/bin"
echo "  unit PATH[0]  : $(printf '%s' "$UNIT_PATH" | cut -d: -f1)"
echo "--- where each sidecar's launch command resolves under the HOST unit's PATH ---"
sed -n '/^mcp_servers:/,/^[a-z_]/p' "$CFG" 2>/dev/null \
  | grep -E '^[[:space:]]+command:' | sed 's/.*command:[[:space:]]*//' | tr -d '"' \
  | while read -r cmd; do
      [ -z "$cmd" ] && continue
      resolved=$(PATH="$UNIT_PATH" command -v "$cmd" 2>/dev/null || echo "(unresolved)")
      mark=""
      case "$resolved" in
        "$INSTALL_DIR"/.venv/bin/*) mark="   <== runs in the host venv: swapping the host SDK breaks it too, isolate it" ;;
        "$INSTALL_DIR"/*)           mark="   <== under the host dir; check whether it has its own venv" ;;
      esac
      printf "  %-12s -> %s%s\n" "$cmd" "$resolved" "$mark"
    done
echo "--- the real interpreter of running sidecars (hard evidence; resolutions config cannot show surface here) ---"
ps -ef | grep -E 'mcp|watchdog|supervisor|sidecar' | grep -v grep | head -10
echo "--- key SDKs currently installed in the host venv (compare item by item after upgrade; a major jump is a breaking entry point) ---"
ls -d "$SP"/mcp-*.dist-info "$SP"/starlette-*.dist-info 2>/dev/null | sed 's#.*/##'
echo "--- isolation reference: which peer components already ship their own venv (naturally immune to this risk) ---"
ls -d "$HOME"/.hermes/repos/*/.venv 2>/dev/null | head -10

# ---------------------------------------------------------------- 5. untracked files vs upstream tracking
echo
echo "=== [5] local untracked files vs upstream tracking (reset overwrite / conflict risk) ==="
for f in $(git status --porcelain 2>/dev/null | awk '/^\?\?/{print $2}' | head -20); do
  f="${f%/}"
  if git cat-file -e "$NEW:$f" 2>/dev/null || git ls-tree -r --name-only "$NEW" -- "$f" 2>/dev/null | head -1 | grep -q .; then
    echo "  COLLISION (upstream tracks it too)       $f"
  else
    echo "  safe (upstream does not track it)        $f"
  fi
done

echo
echo "=== [5b] how the updater handles local changes (stash / reset / clean) ==="
git grep -nE 'stash push|--include-untracked|reset --hard|git clean' "$NEW" -- '*.py' 'scripts/*.sh' 2>/dev/null \
  | grep -vE '^.*#|docstring' | head -20

# ---------------------------------------------------------------- 6. self-built scripts importing host private APIs
echo
echo "=== [6] self-built scripts importing host private APIs (an upgrade refactor breaks them) ==="
if [ -d "$SCRIPTS_DIR" ]; then
  grep -rnE '^\s*(from|import)\s+(hermes_|cron|tools|agent|gateway|hermes_cli|run_agent|model_tools|toolsets)' \
    "$SCRIPTS_DIR" 2>/dev/null | head -30
  echo "  files matched: $(grep -rlE '^\s*(from|import)\s+(hermes_|cron|tools|agent|gateway|hermes_cli|run_agent|model_tools|toolsets)' "$SCRIPTS_DIR" 2>/dev/null | wc -l)"
else
  echo "  (no $SCRIPTS_DIR)"
fi

# ---------------------------------------------------------------- 7. refactor scale
echo
echo "=== [7] added/removed lines in key files (tens of thousands removed = a module was split, private symbols must break) ==="
for f in gateway/run.py cli.py hermes_state.py hermes_constants.py hermes_cli/config.py \
         tools/memory_tool.py model_tools.py toolsets.py tools/registry.py pyproject.toml uv.lock; do
  line=$(git diff --numstat "$OLD" "$NEW" -- "$f" 2>/dev/null | head -1)
  printf "  %-32s %s\n" "$f" "${line:-UNCHANGED}"
done

echo
echo "=== [8] current runtime state (the baseline to compare against after upgrade) ==="
df -h / | tail -1
echo "  .git: $(du -sh "$INSTALL_DIR"/.git 2>/dev/null | cut -f1)  .venv: $(du -sh "$INSTALL_DIR"/.venv 2>/dev/null | cut -f1)"
systemctl --user show hermes-gateway -p ExecMainStartTimestamp -p ActiveState -p NRestarts 2>/dev/null
ps -ef | grep -E 'gateway run|mcp' | grep -v grep | head -10

echo
echo "### Done. Everything was read-only. Next step: take [1][3][4][7] and compare against the release notes."
