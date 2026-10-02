# stdio MCP children: startup cost and restart loops

Depth for Symptom 3 (fails at startup) and Symptom 5 (restart loop). Everything here is a method; the numbers are measurements, so measure your own before quoting them.

## 1. Attribute the cost before you fix it

A stdio child's startup is three separable layers:

| Layer | What it costs | How to tell you are paying it |
|---|---|---|
| Launch layer | `npx`/`npm exec` resolution, `uv run` env resolution, shell wrapper | the process tree has an extra parent (`npm exec`, `sh -c`, `uv`) above the real server |
| Runtime import | MCP SDK + server `main()` | the child prints its own banner in `mcp-stderr.log` only late |
| Handshake | `initialize` + `list_tools` | everything else is quiet and the tool count appears at the end |

Nothing in the logs separate the three for you — time the layers with a probe (`scripts/probe-mcp-handshake.py`) and by spawning the inner binary directly. An entry-point tweak that removes only a 1–3s layer is not the same class of fix as removing a 20s fork layer; only measurement tells you which you hold.

## 2. Reading the restart loop

```bash
# per-server start counts, whole log and one day
for s in <servers>; do printf '%-16s %s\n' "$s" "$(grep -ac "starting MCP server '$s'" ~/.hermes/logs/mcp-stderr.log)"; done

# who is re-adding it
grep -a 'MCP servers reconciled with config' ~/.hermes/logs/agent.log | tail -12

# the failure type at each (re)connect
grep -a "Failed to connect to MCP server" ~/.hermes/logs/agent.log | tail -12
```

- `grep -a` is mandatory on these logs (they carry NUL bytes; a plain grep under-matches silently).
- The reconciler re-adds on a per-server cooldown (30s → 600s backoff), which is why the gaps between `added=['X']` lines grow and then plateau — a growing gap is not evidence that it healed.
- `CancelledError` is the cancellation of the caller's `asyncio.wait_for(..., timeout=connect_timeout)` task, not a server error and not a protocol error. Its neighbours in the same line (`4 failed: ... zhipu_search; bocha_search; metaso_search; feishu`) are the tell that the whole class of stdio servers was cancelled together.
- A successful `registered 7 tool(s)` in the middle of a loop is expected: the loop is defined by the failures, not by a total absence of successes.

## 3. A/B measuring a candidate command

1. Read the server's current `command`/`args` from `config.yaml` (never retype them — you will silently drop a flag).
2. Probe the current form, then each candidate form (tag dropped, version pinned, inner binary spawned directly), same cap per run.
3. Spawn through the real MCP client (`stdio_client` → `ClientSession.initialize()` → `list_tools()`), not `--version` or a bare process launch: only the handshake exercises the SDK import and the transport.
4. Compare tool counts as well as elapsed time — a fast candidate that returns fewer tools is a different server, not an improvement.
5. Mask credential-bearing argv before printing anything.

## 4. Verifying a config change without touching the live config

Copy the real `config.yaml` into a scratch directory and point `HERMES_HOME` at it:

```bash
HERMES_HOME=<scratch> hermes config set <server>.args.1 '<new value>'
diff <real config> <scratch>/config.yaml     # expect exactly one line, same file length
```

This proves the path form works and that the write is surgical, before the live file is touched. Then apply to the live config, re-read the field, and run the runtime's own predicate over the resulting args.

## 5. Editing config is not the same as applying it

The running server owns the `command`/`args` it was spawned with, so an edit reaches it only when the entry is **re-created**. Two measured consequences:

- **A reconnect does not re-read config.** Killing a live server and letting the harness respawn it produced a child still running `npm exec <spec>@latest` minutes after the spec had been edited to drop the tag — the respawn reuses the task's cached config. "Wait for the next reconnect" restores the *old* args; it is not an apply path.
- **The apply state is readable from the process tree, not from the file.** An extra `npm exec` / `sh -c` parent above the real server = the fork layer is still being paid; a direct child of the harness = the fast path is in effect. `mcp-stderr.log` headers cannot distinguish these — both leave the same `starting MCP server '<X>'` line.

To apply an edit to a server that is already live, re-create the entry (`/reload-mcp`, or a gateway restart); the reconciler's add path only helps a server that is *not* live at all. To prove the edit itself before that, time a **fresh-config** spawn in a separate process (`hermes mcp test <server>`, `scripts/probe-mcp-handshake.py --resolved`) — it reads config from disk, so it shows what the next re-created entry will spawn, and it says nothing about whether the gateway is currently serving the tool.

**Call the production resolver by its real name.** `_preflight_stdio_command(server, command, args)` (async: OSV preflight, then the cached-bin swap) is the live call path; prefer it over calling `_npx_cached_bin` directly, and never guess its name — a failed import falls back silently to the raw config, which then measures the **slow** path and reads as if the fix did not work.
