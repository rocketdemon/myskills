#!/usr/bin/env python3
"""Time an MCP stdio handshake for servers configured in <HERMES_HOME>/config.yaml.

Run it with a python that has the `mcp` SDK (the harness venv), e.g.

    ~/.hermes/hermes-agent/.venv/bin/python probe-mcp-handshake.py feishu
    ~/.hermes/hermes-agent/.venv/bin/python probe-mcp-handshake.py feishu --variants
    ~/.hermes/hermes-agent/.venv/bin/python probe-mcp-handshake.py feishu --spec '@scope/pkg'
    ~/.hermes/hermes-agent/.venv/bin/python probe-mcp-handshake.py feishu --resolved

The `--resolved` mode answers "what will the runtime spawn for the config as it is on disk
now" via the production resolver - run it right after a config edit to prove the new args
without waiting for a reconnect (see references/stdio-startup-cost-and-restart-loops.md §5).

Why a handshake probe and not `--version` / a bare launch: only `initialize()` +
`list_tools()` exercise the launch layer, the SDK import, and the transport, which is
exactly what `connect_timeout` is measured against. Compare TOOL COUNTS too - a
candidate that is fast but returns fewer tools is a different server, not a win.

Credential-bearing argv values (values after -a/-s/--token/--secret/... or key=value
pairs) are masked in every line printed. Never print raw argv of a server that takes a
secret as a flag.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time

CRED_FLAGS = {"-a", "-s"}
CRED_HINTS = ("secret", "token", "key", "password", "passwd", "app-id", "app_id", "appid", "credential")


def _is_cred_flag(tok: str) -> bool:
    head = tok.split("=", 1)[0]
    if not head.startswith("-"):
        return False
    low = head.lower()
    return low in CRED_FLAGS or any(h in low for h in CRED_HINTS)


def mask(argv) -> str:
    """Join argv with credential values replaced. Used for EVERY print of a command."""
    out, skip = [], False
    for tok in argv:
        if skip:
            out.append("<masked>")
            skip = False
            continue
        if "=" in tok and _is_cred_flag(tok.split("=", 1)[0]):
            out.append(tok.split("=", 1)[0] + "=<masked>")
            continue
        out.append(tok)
        if _is_cred_flag(tok):
            skip = True
    return " ".join(out)


def hermes_home() -> str:
    return os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")


def load_server(name: str):
    try:
        import yaml  # noqa: PLC0415
    except ImportError:
        sys.exit("PyYAML missing - run this with the harness venv python")
    path = os.path.join(hermes_home(), "config.yaml")
    cfg = yaml.safe_load(open(path, encoding="utf-8")) or {}
    srv = (cfg.get("mcp_servers") or {}).get(name)
    if not srv:
        have = ", ".join(sorted((cfg.get("mcp_servers") or {}).keys())) or "none"
        sys.exit(f"no mcp_servers.{name} in {path} (have: {have})")
    return srv


def _spec_index(args) -> int:
    """Index of the package spec: skip leading -y/--yes."""
    i = 0
    while i < len(args) and args[i] in ("-y", "--yes"):
        i += 1
    return i


def drop_tag(args):
    """Same argv with the version/dist-tag stripped from the spec.

    Scoped names keep their leading '@', so only an '@' AFTER the scope is a tag.
    """
    out = list(args)
    i = _spec_index(out)
    if i >= len(out):
        return out
    spec = str(out[i])
    body = spec[1:] if spec.startswith("@") else spec
    if "@" in body:
        head, _, _tag = body.rpartition("@")
        out[i] = ("@" if spec.startswith("@") else "") + head
    return out


def cached_bin_variant(args):
    """Ask the harness itself whether it would skip npx for these args.

    Returns (command, args) when the harness's own predicate resolves a cached binary,
    else None. Importing it (instead of reimplementing) is the point: the predicate has
    deliberate exclusions and a reimplementation will disagree with the runtime.
    """
    repo = os.environ.get("HERMES_REPO") or os.path.expanduser("~/.hermes/hermes-agent")
    if repo not in sys.path:
        sys.path.insert(0, repo)
    try:
        from tools.mcp_tool_config import _npx_cached_bin  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001 - optional variant
        print(f"  (cached-bin variant unavailable: {type(exc).__name__}: {exc})")
        return None
    hit = _npx_cached_bin(args)
    if not hit:
        return None
    command, rest = hit
    return command, list(rest)


async def resolved_variant(server: str, command: str, args):
    """Ask the harness's own production resolver what it would spawn for this config.

    `tools.mcp_tool._preflight_stdio_command` is the live call path (OSV preflight, then
    the cached-bin swap), so its answer is authoritative for "what will the runtime
    spawn for the args as they are on disk now". Prefer it over `_npx_cached_bin` alone.
    Never guess the resolver's name: a failed import falls back to the raw config, which
    then measures the SLOW path and looks like the fix did not work.
    """
    repo = os.environ.get("HERMES_REPO") or os.path.expanduser("~/.hermes/hermes-agent")
    if repo not in sys.path:
        sys.path.insert(0, repo)
    try:
        from tools.mcp_tool import _preflight_stdio_command  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001 - optional mode
        print(f"  (production resolver unavailable: {type(exc).__name__}: {exc})")
        return None
    try:
        cmd, rest = await _preflight_stdio_command(server, command, list(args))
    except Exception as exc:  # noqa: BLE001 - probe reports, never raises
        print(f"  (production resolver failed: {type(exc).__name__}: {exc})")
        return None
    return cmd, list(rest)


async def probe(label: str, command: str, args, cap: float = 90.0):
    from mcp import ClientSession, StdioServerParameters  # noqa: PLC0415
    from mcp.client.stdio import stdio_client  # noqa: PLC0415

    print(f"  {label}\n    $ {mask([command, *args])}")
    t0 = time.time()
    try:
        params = StdioServerParameters(command=command, args=list(args), env=dict(os.environ))

        async def _run():
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    t = time.time()
                    await session.initialize()
                    handshake = time.time() - t
                    tools = await session.list_tools()
                    return handshake, len(tools.tools)

        handshake, count = await asyncio.wait_for(_run(), timeout=cap)
        print(f"    OK  total {time.time() - t0:6.1f}s  (handshake {handshake:5.1f}s)  tools {count}")
        return count, time.time() - t0, None
    except asyncio.TimeoutError:
        print(f"    TIMEOUT  >{cap:g}s - the child never completed the handshake")
        return None, None, "timeout"
    except Exception as exc:  # noqa: BLE001 - probe reports, never raises
        print(f"    FAIL  {type(exc).__name__}: {str(exc)[:160]}")
        return None, None, type(exc).__name__


async def main() -> int:
    ap = argparse.ArgumentParser(description="Time an MCP stdio handshake.")
    ap.add_argument("server", nargs="?", help="mcp_servers key in config.yaml")
    ap.add_argument("--cap", type=float, default=90.0, help="per-candidate timeout (default 90s)")
    ap.add_argument("--spec", help="override ONLY the package spec (single candidate)")
    ap.add_argument("--command", help="override the command (e.g. node, the harness venv python)")
    ap.add_argument("--variants", action="store_true",
                    help="probe current / tag-dropped / harness cached-bin")
    ap.add_argument("--resolved", action="store_true",
                    help="also probe what the production resolver spawns for the config as it is on disk now")
    ap.add_argument("--json", action="store_true", help="emit machine-readable results")
    ap.add_argument("--list", action="store_true", help="list configured servers and exit")
    opts = ap.parse_args()

    if opts.list:
        import yaml  # noqa: PLC0415
        cfg = yaml.safe_load(open(os.path.join(hermes_home(), "config.yaml"), encoding="utf-8")) or {}
        for name, srv in (cfg.get("mcp_servers") or {}).items():
            print(f"{name:16s} enabled={srv.get('enabled', True)} command={srv.get('command')} "
                  f"connect_timeout={srv.get('connect_timeout')} timeout={srv.get('timeout')}")
        return 0

    if not opts.server:
        ap.error("server name required (or --list)")

    srv = load_server(opts.server)
    command = opts.command or str(srv.get("command") or "")
    args = list(srv.get("args") or [])
    if opts.spec:
        i = _spec_index(args)
        if i < len(args):
            args[i] = opts.spec
        else:
            args.append(opts.spec)

    print(f"server '{opts.server}'  connect_timeout={srv.get('connect_timeout')} "
          f"timeout={srv.get('timeout')}  cap={opts.cap:g}s")

    candidates = [("current", command, args)]
    if opts.variants:
        candidates.append(("tag dropped (spec without version/dist-tag)", command, drop_tag(args)))
        hit = cached_bin_variant(drop_tag(args))
        if hit:
            candidates.append(("harness cached-bin fast path", hit[0], hit[1]))
        else:
            print("  (harness would NOT take the cached-bin fast path for these args)")
    if opts.resolved:
        live = await resolved_variant(opts.server, command, args)
        if live:
            candidates.append(("as the runtime would spawn it (production resolver)", live[0], live[1]))
        else:
            print("  (production resolver unavailable - cannot prove the live spawn path)")

    results = {}
    for label, cmd, a in candidates:
        results[label] = await probe(label, cmd, a, cap=opts.cap)

    counts = {k: v[0] for k, v in results.items() if v[0] is not None}
    if len(set(counts.values())) > 1:
        print(f"\n  WARNING: candidates disagree on tool count {counts} - "
              "a faster candidate with fewer tools is a different server, not an improvement")

    if opts.json:
        print(json.dumps({k: {"tools": v[0], "seconds": v[1], "error": v[2]} for k, v in results.items()},
                         ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
