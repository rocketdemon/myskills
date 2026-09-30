#!/usr/bin/env python3
"""Deterministic probe: is a given dotted module path still shipped in a package version?

Why this exists
---------------
"Do not trust release-note prose about a module being removed." Download the
published artifacts and read their file tables. This is the strongest available
evidence for the class of breakage where an upgrade REMOVES an import path
(not deprecates it), which silently kills any sidecar / third-party server that
imports it.

Verified use: mcp 1.28.1 ships mcp/server/fastmcp/* (19 entries) and
mcp 2.0.0 ships none of it (it moved to mcp/server/mcpserver/*, 21 entries).
An unpinned third-party MCP server importing `mcp.server.fastmcp` therefore
breaks with ModuleNotFoundError the moment the host venv upgrades.

Control-sample rule
-------------------
Always pass at least one version you KNOW has the module (the currently-installed
one is ideal) alongside the target version. If the control also reports absent,
the probe is broken or the path was mistyped -- that is NOT evidence of removal.
This script enforces the rule by labelling the outcome, not by failing silently.

Usage
-----
  probe-wheel-module-presence.py PKG VER... -- MODULE...

  probe-wheel-module-presence.py mcp 1.28.1 2.0.0 -- mcp.server.fastmcp mcp.server.mcpserver

Cross-checking a requirement spec instead of a version:
  probe-wheel-module-presence.py mcp 1.28.1 -- mcp.server.fastmcp
  (a single version with the module present only proves "this version is fine",
   not "the next one is too")

Exit code: 0 always (this is a report, not a gate). Read the VERDICT lines.

Stdlib only. No install, no site-packages mutation, nothing written to disk.
"""
from __future__ import annotations

import io
import json
import sys
import tarfile
import urllib.request
import zipfile

PYPI = "https://pypi.org/pypi/{pkg}/{ver}/json"
UA = {"User-Agent": "wheel-module-presence-probe"}


def fetch(url: str, timeout: int = 90) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def pick_artifact(meta: dict) -> tuple[str, str]:
    """Prefer a pure-python wheel, then any wheel, then the sdist."""
    urls = meta.get("urls") or []
    wheels = [u for u in urls if u["filename"].endswith(".whl")]
    pure = [u for u in wheels if "py3-none-any" in u["filename"] or "py2.py3" in u["filename"]]
    sdist = [u for u in urls if u["filename"].endswith((".tar.gz", ".zip"))]
    for group in (pure, wheels, sdist):
        if group:
            return group[0]["url"], group[0]["filename"]
    raise RuntimeError("no downloadable artifact published")


def list_members(blob: bytes, filename: str) -> list[str]:
    if filename.endswith(".whl") or filename.endswith(".zip"):
        return zipfile.ZipFile(io.BytesIO(blob)).namelist()
    with tarfile.open(fileobj=io.BytesIO(blob)) as tf:
        names = tf.getnames()
    # sdists nest everything under <pkg>-<ver>/; strip that prefix
    if names:
        root = names[0].split("/")[0]
        prefix = root + "/"
        names = [n[len(prefix):] if n.startswith(prefix) else n for n in names]
    return names


def module_present(names: list[str], dotted: str) -> bool:
    base = dotted.replace(".", "/")
    return f"{base}.py" in names or f"{base}/__init__.py" in names


def child_count(names: list[str], dotted: str) -> int:
    base = dotted.replace(".", "/") + "/"
    return sum(1 for n in names if n.startswith(base))


def main(argv: list[str]) -> int:
    if "--" not in argv:
        print(__doc__)
        return 0
    split = argv.index("--")
    head, modules = argv[:split], argv[split + 1:]
    if len(head) < 3 or not modules:
        print(__doc__)
        return 0

    pkg, versions = head[0], head[1:]
    print(f"package : {pkg}")
    print(f"versions: {', '.join(versions)}")
    print(f"modules : {', '.join(modules)}")
    print()

    present: dict[str, dict[str, bool]] = {m: {} for m in modules}
    for ver in versions:
        try:
            meta = json.loads(fetch(PYPI.format(pkg=pkg, ver=ver)).decode("utf-8"))
            url, fname = pick_artifact(meta)
            names = list_members(fetch(url), fname)
        except Exception as e:  # noqa: BLE001
            print(f"[FAIL] {pkg} {ver}: {type(e).__name__}: {e}")
            for m in modules:
                present[m][ver] = False
            continue
        print(f"[ok]   {pkg} {ver}: {fname} ({len(names)} entries)")
        for m in modules:
            hit = module_present(names, m)
            present[m][ver] = hit
            extra = f"  (+{child_count(names, m)} under {m}/)" if hit and child_count(names, m) else ""
            print(f"         {'PRESENT' if hit else 'absent ':8s}  {m}{extra}")
        print()

    print("=" * 70)
    failures = 0
    for m in modules:
        flags = present[m]
        hits = [v for v in versions if flags.get(v)]
        if len(hits) == len(versions):
            print(f"VERDICT {m}: shipped in ALL probed versions -> NOT removed.")
            if len(versions) == 1:
                print("        (single version probed: this does not prove the NEXT one ships it)")
        elif not hits:
            failures += 1
            print(f"VERDICT {m}: absent in ALL probed versions -> NOT EVIDENCE of removal.")
            print("        Either the dotted path is mistyped, or no control sample was supplied.")
            print("        Re-run with a version you KNOW has it (the currently-installed one).")
        else:
            absent = [v for v in versions if not flags.get(v)]
            print(f"VERDICT {m}: CHANGED -- present in {hits}, absent in {absent}.")
            print("        Anything importing this path will break on the absent versions.")
    print("=" * 70)
    if failures:
        print(f"WARNING: {failures} module path(s) had no positive control. Results inconclusive for those.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
