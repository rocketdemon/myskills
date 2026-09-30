#!/usr/bin/env python3
"""
Decide whether a preset config value survives the upgrade migration.

Background (empirical result; see references/config-migration-rewrite-semantics.md):
The migration `_rewrite_stale_default(old=…, new=…)` triggers only when the
**current value == the old default value**.
  - A preset that is != the old default => the migration skips it => it survives
    the upgrade => it can be scheduled as a "pre-upgrade action"
  - A preset that is == the old default => the migration rewrites it anyway => it
    can only be scheduled as a "post-upgrade action"
This script actually runs `run_migrations()` on the **target version's code** to
confirm or refute that, instead of inferring it by reading the code.

Usage:
  python3 probe-config-migration-survival.py --config ~/.hermes/config.yaml --code /tmp/probe-vX

  # Customize the keys to watch and the presets to inject:
  python3 probe-config-migration-survival.py --config ... --code ... \
      --watch delegation.max_iterations,curator.stale_after_days \
      --preset delegation.max_concurrent_children=4 --preset curator.stale_after_days=31

Output: a BEFORE/AFTER table per case plus the list of "changed keys", and finally
the sha256 of the real config (to prove the probe never touched the production
config). The production config is read-only: every case works on its own copy, run
in a fresh temp directory, and **nothing is ever deleted**.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

DEFAULT_WATCH = [
    "delegation.max_iterations",
    "delegation.max_concurrent_children",
    "curator.stale_after_days",
    "curator.archive_after_days",
    "display.background_process_notifications",
    "model_catalog.ttl_hours",
    "model_catalog.ttl_minutes",
    "_config_version",
]


def sha16(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()[:16]


def getk(d, path):
    cur = d
    for p in path.split("."):
        if not isinstance(cur, dict) or p not in cur:
            return "<absent>"
        cur = cur[p]
    return cur


def setk(d, path, val):
    cur = d
    parts = path.split(".")
    for p in parts[:-1]:
        cur = cur.setdefault(p, {})
    cur[parts[-1]] = val


# ---------------------------------------------------------------- worker mode
def run_worker(args):
    """Run the target-version migration engine inside an isolated HERMES_HOME."""
    import yaml

    home = os.environ["PROBE_HOME"]
    cfg = os.path.join(home, "config.yaml")
    with open(cfg, encoding="utf-8") as fh:
        d = yaml.safe_load(fh) or {}

    for spec in args.preset or []:
        k, _, v = spec.partition("=")
        try:
            v = json.loads(v)          # parses numbers / booleans / arrays
        except json.JSONDecodeError:
            pass
        setk(d, k, v)

    sys.path.insert(0, args.code)
    import yaml as _y  # noqa: F401

    before = {k: getk(d, k) for k in args.watch}
    with open(cfg, "w", encoding="utf-8") as fh:
        yaml.safe_dump(d, fh, allow_unicode=True, sort_keys=False)

    from hermes_cli.config import check_config_version
    from hermes_cli import config_migrations as cm

    cur, latest = check_config_version()
    results = {"config_added": [], "config_removed": [], "warnings": [], "errors": []}
    cm.run_migrations(cur, results, quiet=True)

    with open(cfg, encoding="utf-8") as fh:
        after = yaml.safe_load(fh) or {}

    print(f"    schema version: {cur} -> {latest} (run_migrations does not bump the stamp; do not use the stamp to decide whether it ran)")
    moved = []
    for k in args.watch:
        b, a = before[k], getk(after, k)
        flag = "   <-- CHANGED" if b != a else ""
        if b != a:
            moved.append(k)
        print(f"    {k:<50} {b!r} -> {a!r}{flag}")
    print(f"    changed keys: {moved if moved else '(none)'}")
    print(f"    migrations reported {len(results.get('config_added', []))} added entry(ies)")
    return 0


# ---------------------------------------------------------------- main mode
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True, help="production config (read-only; it is copied)")
    ap.add_argument("--code", required=True, help="target-version code directory (worktree root)")
    ap.add_argument("--watch", default=",".join(DEFAULT_WATCH))
    ap.add_argument("--preset", action="append", default=None,
                    help="KEY=VALUE; repeatable; the case name is derived from these presets")
    ap.add_argument("--case", action="append", default=None,
                    help="append one extra control case that presets nothing")
    ap.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args()
    args.watch = [w.strip() for w in args.watch.split(",") if w.strip()]
    args.preset = args.preset or []

    if args.worker:
        return run_worker(args)

    if not os.path.isfile(args.config):
        print(f"FATAL: config not found: {args.config}")
        return 1
    if not os.path.isdir(args.code):
        print(f"FATAL: target code directory not found: {args.code}")
        return 1

    real_sha, real_mtime = sha16(args.config), os.path.getmtime(args.config)
    print("=" * 90)
    print(f"production config = {args.config}")
    print(f"   sha256[:16] = {real_sha}")
    print(f"   mtime       = {real_mtime}")
    print(f"target-version code = {args.code}")
    print("=" * 90)

    root = tempfile.mkdtemp(prefix="migration-preset-probe-")
    print(f"probe root = {root} (fresh; this script deletes nothing)\n")

    cases = []
    if args.preset:
        cases.append(("preset:" + "+".join(args.preset), list(args.preset)))
    # control group: nothing preset
    cases.append(("control:nothing-preset", []))
    for c in (args.case or []):
        cases.append((c, []))

    for name, presets in cases:
        home = os.path.join(root, name.replace("/", "_").replace(":", "_"))
        os.makedirs(home, exist_ok=True)
        shutil.copy2(args.config, os.path.join(home, "config.yaml"))
        print("=" * 90)
        print(f"### case = {name}")
        print("=" * 90)
        cmd = [sys.executable, os.path.abspath(__file__),
               "--worker", "--config", args.config, "--code", args.code,
               "--watch", ",".join(args.watch)]
        for p in presets:
            cmd += ["--preset", p]
        env = dict(os.environ, HERMES_HOME=home, PROBE_HOME=home,
                   PROBE_CODE=args.code)
        r = subprocess.run(cmd, env=env, capture_output=True, text=True)
        print(r.stdout.rstrip() or "(no stdout)")
        if r.stderr.strip():
            print("    stderr:", r.stderr.strip()[:500])
        print()

    # closing check: prove the production config was never touched
    print("=" * 90)
    print("Production config closing check (must be identical to the start)")
    print(f"   sha256[:16] = {sha16(args.config)}   {'OK unchanged' if sha16(args.config) == real_sha else '!!! MODIFIED !!!'}")
    print(f"   mtime       = {os.path.getmtime(args.config)}")
    print("=" * 90)
    print("\nHow to read this: compare a 'preset != old default' case against the control --")
    print("  if the preset value is unchanged in AFTER (while the same key changed in the control), that confirms 'the preset survives the migration'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
