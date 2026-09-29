---
name: mcp-diagnostics
description: Diagnose missing MCP tools, stale config, and huge returns.
version: 1.0.0
author: "rocketdemon"
license: MIT
metadata:
  hermes:
    tags: [MCP, Tools, Diagnostics]
    related_skills: [native-mcp]
---

# MCP Tool Availability Diagnostics

Complements `native-mcp` (the official MCP client documentation) with field diagnostics that it does not cover. `native-mcp` explains configuration and standard failures; this skill covers two real failure classes: "**the process is alive but the tool never registered**" and "**a check burns an enormous amount of tokens**".

> **Scope**: the diagnostic paths below assume **Hermes Agent** (`~/.hermes/...` paths, `hermes mcp test` / `hermes gateway restart` commands). The mechanism-level judgements hold on any harness: reading PPID/cgroup to detect an orphaned stdio child, reading log-header signatures to tell whether the child reached `main()`, comparing cold-start duration against `connect_timeout` headroom, and minimizing tool returns **at the parameter level**. Every concrete number below (timeout seconds, payload sizes, durations) is a **measured historical value** — read yours from your own config, do not copy these as constants.

## When to Use

- An MCP tool cannot be found by `tool_search` / `tool_describe`, yet the process is alive (`pgrep`)
- You just changed MCP config, or just upgraded, and the tool is still missing (**there is no server process at all**) → jump straight to "Symptom 4"
- Token spend explodes after an infrastructure check / tool sweep
- An MCP tool call returns an abnormally large payload (hundreds of KB, even MB)

## Symptom 1: tool not registered (process healthy, tool missing)

**What you see**: the process is alive, `config.yaml` has the entry, the source clearly registers the tool (e.g. `@server.tool()`), but `tool_search` cannot find it and `tool_describe` reports not found.

**First confirm the server itself is fine** — do not reach for "version" or "config" yet; do an independent handshake with the official client:

```python
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
# run this with the venv python of the server, or with Hermes's venv python
# async with stdio_client(params) -> ClientSession -> await session.initialize(); await session.list_tools()
```

If `list_tools()` returns more than 0 tools, the server is fine and the problem is on the gateway's side of the connection. Continue.

**Root-cause discrimination (bocha case, 2026-08-26)**: the gateway-side connection process for that server died; the stdio server became an orphan, was re-parented by systemd, and its stdio pipe broke (it does not exit on stdin EOF). The gateway marked it failed and never reconnected.

```bash
ps -o pid,ppid,stat,cmd -p <server_pid>
cat /proc/<server_pid>/cgroup
```

- **Healthy**: PPID = the gateway process PID (or the server is wrapped by `mcp_stdio_watchdog.py`, whose PPID is the gateway)
- **Broken**: PPID = 1 or systemd (the PID of `systemd --user`), while the cgroup is still `hermes-gateway.service` — the original parent died, the server was orphaned, and the pipe is broken

**Compare against other servers with the same configuration**: those wrapped by `mcp_stdio_watchdog.py` (e.g. metaso/feishu, PPID still the gateway) keep their connection; those without a wrapper (e.g. bocha running `uv run` directly, no watchdog) fall apart the moment the connection drops and become orphans.

**Fix**: restart the gateway to re-run discovery (a destructive operation — follow the interception procedure). Note: Hermes blocks restarting/stopping the gateway **from inside the gateway process** (SIGTERM propagates to the child processes and kills the command), reporting `Run 'hermes gateway restart' from a separate shell outside the running gateway`. An agent cannot do this step for the user — the user must run `hermes gateway restart` or `systemctl --user restart hermes-gateway` manually in a WSL terminal (outside the gateway), then come back and send a message so you can verify.

## Symptom 4: config changed but the tool is still missing (bad value type / never reloaded)

When there is **no server process at all** in the process table, separate these two cases before considering a gateway restart.

### 4a. A list value was written as a string (old SDK tolerated it, new SDK rejects it outright)

`hermes config set <server>.args []` writes the value as a **quoted string** `args: '[]'` instead of a YAML empty list.
The old SDK (1.x) tolerated it and the server started normally; after upgrading to **mcp 2.0.0, pydantic's strict validation** rejects it:

```text
✗ Connection failed: 1 validation error for StdioServerParameters
  args
  Input should be a valid list [type=list_type, input_value='[]', input_type=str]
```

- **Test**: `grep -n 'args:' ~/.hermes/config.yaml` — a normal entry is an `args:` block or `[]`; a **quoted `'[]'` is broken**.
- **Fix**: `hermes config unset <server>.args` (drop the key → falls back to the default empty list). Do not gamble on `config set` guessing the type again.
- **Judge**: `hermes mcp test <server>`. `hermes mcp list` only reports configured state and **always shows `✓ enabled`, even for a dead connection**.

Same class: any `config set` that writes a list or a boolean must be read back and type-checked (`off` being parsed as boolean `false` is **normal**;
a quoted list is **not**).

### 4b. The gateway does not auto-reload — and the only reload command has a confirmation gate

The gateway **has no config-change watcher**: after editing config, the running gateway keeps its old connections and its old tool surface. The only non-restart path is
`/reload-mcp`, which **asks for confirmation first** (a reload invalidates the provider's prompt cache), and a pending confirmation **expires after 300 seconds** —
**a late reply is silently dropped: nothing executes, nothing is answered, nothing is logged** (it looks like "I sent approve and nothing happened").

Mechanism details, workarounds, and "how to tell what the user actually sent" are in `references/reload-mcp-and-slash-confirm.md`.

**"Did it actually reload?" — read all three, or you may misjudge**:

```bash
grep -a 'starting MCP server' ~/.hermes/logs/mcp-stderr.log | tail -12   # any new start event?
pgrep -af '<server executable name>'                                     # is the process there?
pgrep -af 'hermes_cli.main gateway run'                                  # did the gateway PID change?
```

**The `-a` in `grep -a` is not optional**: these logs contain NUL bytes, and a plain grep / `search_files` treats them as binary and **silently under-matches** (it looks like "the log has no such line" rather than erroring). Always `grep -a` for logs; `search_files` is for config/source (no NULs).

**Do not use the success/failure of `hermes mcp test <server>` as evidence about whether the gateway has this tool** — it opens **its own separate connection**:
success does not prove the gateway-side connection exists, and failure does not prove the gateway is broken (cold start, see Symptom 3). The gateway's tool surface can only be judged by the three commands above.

## Symptom 2: a huge payload blows up token usage

**What you see**: after a check / sweep, the user reports abnormally large single-day token spend.

**Root cause**: the tool call itself pulls a huge payload into context (measured with jike: `fence_poi_list` at radius=1000 returned 977KB, `heat_map` returned 569KB, `grid_datas` returned tens of thousands of characters of audience profiles). "Just look at biz_code and do not expand" is **not enough** — the moment the tool is called the payload is already in context, so it must be minimized **at the parameter level**.

**Principle**: when checking/probing tool availability, use the **minimal parameters** that still produce a success response. The goal is the success flag (biz_code=0/200, non-empty data), not the full payload.
- Fence/radius tools: use the smallest radius (e.g. 100m instead of 1000m)
- Grid/heat tools: smallest cell_level + small radius
- List tools: smallest page_size / count
- Profile/aggregate tools: skip them, or call once with the smallest scope

## Symptom 3: fails at startup, no child output in mcp-stderr.log (CancelledError)

**What you see**: `errors.log` repeatedly shows `Failed to connect to MCP server 'X' (command=uv): CancelledError`, and in `~/.hermes/logs/mcp-stderr.log` there is **no child output** after that server's start header (not even the server's own banner).

**Read the header signature** (the single most useful localization technique):

```text
===== [2026-09-08 11:40:44] starting MCP server 'bocha_search' =====
Starting Bocha Search MCP server...          <- this line present = the child reached main()
```

- Header **immediately followed** by the server's own banner (e.g. `Starting Bocha Search MCP server...`) = the child executed into `main()` and started fine; the problem is after the handshake/registration.
- Header followed by **nothing** (jumping straight to the next server's header) = the child never reached `main()`. For `command: uv ... run <pkg>` shapes, this means `uv run` is stuck in environment resolution and the Python process never spawned.

The header is written by `_write_stderr_log_header` after the OSV pre-check and before `stdio_client` spawns. So "header but no banner" ≈ spawn failed or `uv run` is stuck; "no header" ≈ it never got as far as spawn (it died in an earlier phase).

**What CancelledError means**: `_discover_and_register_server` wraps the connection as `asyncio.wait_for(_connect_server(...), timeout=connect_timeout)`; after the timeout the task is cancelled and surfaces as `CancelledError`. It is **not a signal of a config error** — most often the child started slowly/was stuck and the stdio handshake did not finish within connect_timeout.

**Elimination procedure (do this before any config/code conclusion; verify every step by hand)**:
1. `uv run <pkg>` (with the key) → prints the banner = code / uv / venv are fine
2. `.venv/bin/python -c "from <pkg> import main; main()"` → bypasses `uv run`, validates the server code itself
3. `env -i PATH=... HOME=... USER=... LANG=... <key> uv run <pkg>` → simulates the gateway's minimal filtered env, proves the filtered env does not break startup
4. `python tools/mcp_stdio_watchdog.py --ppid $$ -- <full command>` → proves the watchdog wrapper is fine

If all of the above pass and it still fails, do not rush to attribute it to "stuck/deadlock". Use **historical successful startup duration** to establish the real cold-start length:

```bash
# interval from the successful header timestamp to the banner timestamp = cold-start duration
grep -n 'starting MCP server' ~/.hermes/logs/mcp-stderr.log | tail -6
```

Measured bocha cold start: 18~26s (the bulk is **importing the MCP SDK**, not uv resolution). Compare that against the server's `connect_timeout` (commonly 30s) — headroom is only 4~12s, easily exceeded on a slow network or under concurrency. **The root cause is "slow cold start + too-tight connect_timeout", not "uv hanging".**

**The real fix is raising `connect_timeout`** (30→90; **npx-based servers need ≥120** — measured feishu (`npx -y lark-mcp@latest`, whose cold start must check the network for the latest version) took **69.2s** to connect, leaving only 21s of headroom under its then-90s limit). **Do not change the `.venv` entry point** — that only saves the 1~3s of uv resolution and cannot save the ten-plus seconds of MCP SDK import, so it does not fix the root cause (a `verify-conclusion` recheck overturned that approach). To change config, `write_file` a temporary script and run it with `terminal` (editing config through a heredoc triggers an approval timeout); after the change, verify by reading the field back with `read_file`.

## MCP log file map

| File | Contents | Diagnostic value for MCP |
|---|---|---|
| `~/.hermes/logs/mcp-stderr.log` | stderr of each stdio MCP child, with a `===== starting MCP server 'X' =====` header | **Core**: the header signature tells you whether the child got up |
| `~/.hermes/logs/errors.log` | WARNING/ERROR, including `Failed to connect to MCP server 'X' (command=...): <err>` | **Core**: the connection-failure type (CancelledError / TimeoutError / ...) |
| `~/.hermes/logs/gateway.log` | platform connect/disconnect (weixin/feishu connected...) | No MCP discovery logging; grepping for MCP comes up empty |
| `~/.hermes/logs/agent.log` | agent session loop (API calls, tool calls) | No MCP discovery logging |

MCP discovery details live only in mcp-stderr.log + errors.log. Do not go looking in gateway.log / agent.log.

## Pitfalls

- **A live process does not mean the tool registered.** After the stdio connection breaks, the server becomes an orphan and keeps running; you must judge by PPID/cgroup, not by `pgrep` finding a process.
- **A version mismatch is usually not the root cause.** An mcp 1.28.1 client ↔ 1.6.0 server handshake succeeded and returned tools; rule out connection/process problems first, then suspect the protocol version.
- **The watchdog wrapper applies unconditionally to every POSIX stdio server** (`_wrap_command_with_watchdog`, `mcp_tool.py` around line 2699). Not seeing a server's watchdog in the process list usually means it already failed and was reaped, not that "it was never wrapped". Do not use "is there a watchdog" as a root-cause criterion — the "bocha has no watchdog" note recorded on 08-26 in Symptom 1 was a misjudgement; it was actually "reaped after failing".
- **`pgrep -f '<pattern>'` matches your own shell command.** When the command line contains the pattern string, `pgrep -f` matches itself; filter out `bash -c`, `hermes-snap` and the `grep` itself before counting, or use `ps aux | grep ... | grep -v grep | grep -v 'bash -c'`.
- **Secrets in config.yaml are redacted in read_file / search_files / terminal output** (shown as `sk-abc...1234`, an illustrative example — never reproduce a real prefix/suffix). To check whether a key is complete, measure its length with `awk '{print length($0)}'` (e.g. 35 characters) instead of reading the content — display-layer redaction hides "missing" and "normal" equally well.
- **`bocha_ai_search` reporting `Unexpected error: 'datePublished'` is a server-side bug** (its server.py parses the ai-search response and force-reads a `datePublished` field; ai-search and web-search return different structures, so the missing field raises KeyError). **Workaround: use `bocha_web_search`** — semantically close, and web_search is usually sufficient. Do not treat it as "bocha is entirely broken" — only the ai_search endpoint has this field problem.
- **A failed first connectivity round does not mean broken; retest once before concluding.** Measured after an upgrade: one round of `hermes mcp test` showed **3/5 timeouts** (metaso 30s / feishu 90s / zhipu 30s), and the **retest passed everything** (metaso 13.2s / feishu 69.2s / zhipu 9.8s / bocha 8.3s / jike 6.5s) — the first round is cold start, the second is steady state. Misjudging this step sends you off to change things that were never broken.
- **Evidence for "the tool surface is missing X" must come from the gateway side (process + mcp-stderr.log + gateway PID), never from the tool list in your own context** — that is a snapshot from context build time and may predate the fix you just made. Conversely, `pgrep` finding no process + no `starting MCP server` event in the log is what "it really did not come up" looks like.
