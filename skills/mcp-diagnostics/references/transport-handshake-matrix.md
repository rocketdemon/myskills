# Transport handshake matrix: stdio / SSE / streamable HTTP

The one functional check that costs nothing: `initialize` + `list_tools()` against the live server.
It proves the protocol answers and enumerates the exposed tools **without executing a search**, so it
spends no API quota. That is the right answer to "the process is alive — but is the tool usable?".
Real retrieval ("does the upstream key work and return data?") is a different, paid check; do not
conflate them, and do not claim the free one covers the paid one.

Verified against the Hermes venv interpreter (`~/.hermes/hermes-agent/.venv/bin/python`, mcp 2.0.0).
Read each server's spec from `config.yaml` `mcp_servers` and pick the branch by its keys:

| spec keys | transport | client factory |
|---|---|---|
| `command` (+ `args`, `env`) | stdio | `mcp.client.stdio.stdio_client(StdioServerParameters(...))` |
| `url` + `transport: sse` | SSE | `mcp.client.sse.sse_client(url=..., headers=...)` |
| `url` (no `transport`) | streamable HTTP | `mcp.client.streamable_http.streamable_http_client(url, http_client=...)` |

## SDK 2.0 shapes (the part that costs an hour when guessed)

- The streamable-HTTP factory is **`streamable_http_client`**; the 1.x name `streamablehttp_client` no
  longer exists in `mcp.client.streamable_http` on mcp 2.0.0. Hermes' own client tolerates either name
  (`tools/mcp_tool.py` `_MCP_SDK_LAZY_SYMBOLS`), so a hand-written probe must branch or pin — and an
  `ImportError` there is a **probe** bug, not a finding about the server.
- **`streamable_http_client` takes no `headers` parameter.** Auth goes through an HTTP client you build
  and hand in; the caller owns its lifecycle:

```python
import httpx2
from mcp.client.streamable_http import streamable_http_client

async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=30.0) as hc:
    async with streamable_http_client(url, http_client=hc) as res:
        read, write = res[0], res[1]          # yields a 2-tuple of streams
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
```

- `sse_client(url=..., headers=...)` **does** accept headers directly and also yields a 2-tuple.
- stdio: pass `env=dict(os.environ) | spec_env`. Servers spawned through `npx`/`uv` need the ambient
  `PATH`/`HOME`; a hand-built minimal env makes them fail in a way that reads as a server bug.
- Wrap each step in `asyncio.wait_for(budget)` **and** cap the whole probe from outside: a hung
  transport otherwise hangs its caller — and inside a boot check, that hangs the whole run.

## Cost and duration measured on this host

Serial `initialize` + `list_tools`, one server at a time: stdio bocha 9.8s, stdio metaso 2.6s,
stdio feishu 31.3s (`npx` cold start dominates), SSE jike 0.6s; the streamable-HTTP branch was verified
successful but not separately timed — all five land around 45–50s. Give `npx`-based servers a ≥90s
per-item budget, keep the total below the caller's own timeout, and treat these as historical values
rather than constants.

Warm end-to-end (handshake + the paid call below, all five, serial) measures **16.8–20.2s**; the cold
`npx` feishu start is what makes the first run after a boot the slow one.

## Two-tier reporting: liveness vs availability (never merge them into one line)

Two independent judgments, and a report that merges them hides the dangerous one:

| tier | proves | how |
|---|---|---|
| liveness | the process exists / the endpoint answers 200 | `pgrep -fc <server>` for stdio, `curl -o /dev/null -w '%{http_code}'` for HTTP/SSE |
| availability | the protocol answers **and** a tool really returns data | `initialize` + `list_tools()` (free) → one minimal real call (paid) |

A process that is alive but hung passes `pgrep` and fails the handshake; a server whose upstream key was
revoked passes the handshake and `list_tools()` and fails only the real call. Print two lines with
independent counts:

```text
✅ MCP liveness 5/5 (bocha·metaso·feishu·zhipu·jike) [pgrep + HTTP 200]
✅ MCP availability 5/5 (handshake 5/5 · real retrieval 5/5 · tool counts 1·2·2·7·6 · 17s)
⚠️ MCP availability 4/5 (handshake 5/5 · real retrieval 4/5) — jike: real retrieval timed out (>30s)
```

Rules that keep the report honest:
- **Never render liveness as availability.** One ✅ covering both teaches the reader the wrong thing;
  the failure it hides (process alive, tool dead) is exactly the one that costs a day to find later.
- **A check that could not run must say so.** Print `UNTESTED (not passed)` with a reason; when only some
  items were probed print `⚪ only N/M probed — coverage insufficient`. Otherwise a silent skip and an
  all-green run are byte-identical in the output.
- **Budget exhaustion is its own outcome**, distinct from failure: mark untouched items `UNTESTED`,
  never count them as passing, and do not let one hung server eat the budget silently.

## Paid real-retrieval calls at minimum parameters

Pick the cheapest tool that still exercises the upstream credential, and pin the size limits — a probe
returning 500KB is a cost incident, not a check (jike's fence/grid/heat tools returned 977KB/569KB).

| server | call | why this one |
|---|---|---|
| stdio bocha | `bocha_web_search {query, count: 1}` | smallest result count; avoid `bocha_ai_search` (server-side `datePublished` bug) |
| stdio metaso | `metaso_search {query, size: 1, include_summary: false}` | suppress the summary field |
| stdio feishu | `bitable_v1_appTable_list {path: {app_token: <known base>}}` | read-only, no model tokens; lark tools all need an `app_token` — reuse a base you already have |
| HTTP zhipu | `web_search_prime {search_query}` | only required field |
| SSE jike | `post_region_insight_poi_location {keyword, page_size: 1}` | deliberately **not** `fence_poi_list` / `grid_datas` / `heat_map` |

Read required fields off the live server instead of guessing: `list_tools()` carries each tool's input
schema under **`input_schema`** (not `inputSchema`) on mcp 2.0.0 — reading the wrong attribute silently
returns `{}` and makes every tool look argument-free.

## Instrument-side FAIL: the probe must spawn what production spawns

An availability probe answers exactly one question: *would the thing production runs have worked?* On this host
those two were not the same thing, and the difference produced an alarm that reads byte-for-byte like an outage.

| | what it spawns | measured cold start |
|---|---|---|
| runtime (gateway) | `npx` resolved once → the **cached binary** `~/.npm/_npx/<hash>/node_modules/.bin/lark-mcp mcp -t preset.base.default` | paid once |
| probe (before the fix) | `command`/`args` straight from config = `npx -y @larksuiteoapi/lark-mcp …`, re-resolving every run | **90.7s** |

Boot is the worst moment to sample: cold page cache plus this host's mechanical disk. The probe's own feishu
handshake cap is 90s, so a 90.7s cold spawn renders as `feishu: handshake FAIL · real retrieval FAIL` while the gateway's
lark-mcp process is healthy — a re-measure minutes later gave handshake OK · tool count 7 (32s cold, then 9.4s warm).
Same-run decay (90.7 → 64.9 → 23.1 → 16.1 → 9.2 → 6.8s) is the signature of cold reads, not of death.

Rules:
- **A FAIL from a self-spawned process is a hypothesis about the probe first.** Before reporting a component
  down, check that the probe's spawn path matches the runtime's (`pgrep -af <binary>`, compare `command`+`args`).
- **First FAIL → retry once warm, and leave a trace.** One boot-time sample is the least trustworthy sample of
  the day. The retry must not be silent: print `⚪ <server>: first attempt FAIL, warm re-test passed (first 90.0s … → re-test 9.4s)`
  so "rescued by a retry" and "was never retried" stay distinguishable, and print `budget exhausted, not re-tested` when the
  remaining budget forbids the retry. Retry caps are discounted (`MCP_FUNC_RETRY_SCALE`, default 0.6) so a
  genuinely dead server cannot eat the whole budget.
- **Never let the wrapper be the reason.** The timeout is raised inside anyio's TaskGroup, so it arrives as
  `ExceptionGroup: unhandled errors in a TaskGroup (1 sub-exception)`; `except asyncio.TimeoutError` never fires
  and the report shows a message that reads like a crash — indistinguishable from a real outage. Unwrap the
  group (walk `e.exceptions`) and force the human sentence to say `timeout (handshake stage …)`. Keep non-timeout groups
  honest: a missing `npx` must still read `FileNotFoundError: …`, never "timeout".
- Why it matters: a false ⚠️ costs the same attention as a real one and trains the reader to ignore the line.
  Here the instrument, not the service, was the defect.

## Reusable artifacts on this host

- `~/.hermes/scripts/mcp-func-check.py` — the two-tier check over everything in `mcp_servers`:
  `--report` prints the availability line, `--json` the per-item detail, `--selftest` the renderer +
  exception-classification + retry-trace cases, `--only a b` narrows the run and prints the coverage caveat.
  Timeouts and retry are env-overridable (`MCP_FUNC_HS_TIMEOUT` / `MCP_FUNC_CALL_TIMEOUT` / `MCP_FUNC_BUDGET` /
  `MCP_FUNC_RETRY` / `MCP_FUNC_RETRY_SCALE`) so tests stay fast. Retry is on by default; `MCP_FUNC_RETRY=0`
  reduces it to a single attempt.
- `~/.hermes/scripts/test-mcp-func-check.py` — bidirectional suite: the real config must show 5/5;
  a server whose `command` is `/bin/sleep 600` (process alive, protocol dead) must **not** print ✅;
  a wrong tool name must render `handshake 1/1 · real retrieval 0/1` (proving the tiers stay separate); a bad URL
  must fail; `--only` must print the coverage caveat; a real handshake timeout must render `timeout` and must
  **not** contain `ExceptionGroup`; the retry path is exercised with a monkeypatched probe (fail→pass must
  count as passing **and** stay visible, fail→fail must stay non-passing and print `re-test still FAIL`, and
  `MCP_FUNC_RETRY=0` must reduce it to exactly one attempt). Every case must exit 0 — a check that can hang the
  boot run is worse than no check.
- Wired into `infra-check-boot.sh` §6b. The liveness judge stays in the shell: one implementation per
  judgment, never two.

## Verifying a change to this check (the change is not done until the scheduler ran it)

1. **Syntax, then an isolated run.** `bash -n infra-check-boot.sh`, then run it with a TEMPORARY dedup marker
   (`INFRA_CHECK_MARKER=/tmp/marker-$$`) so production state is untouched, and read the very lines you changed.
2. **Judge-level tests in BOTH directions.** `mcp-func-check.py --selftest` (renderer cases, no network) and
   `test-mcp-func-check.py` (real CLI path). A predicate change verified only on the happy path is untested:
   the negative case (process alive, protocol dead → must NOT print ✅) is the whole point of the change.
3. **A manual run is not the scheduler's run.** Trigger the owning job once (`cronjob_manage action="run"`) and
   read three artifacts: the newest file under `cron/output/<job>/` (**>200B = actually delivered**; a ~159B
   header-only file is the silent path), plus `jobs.json`'s `last_status`, `last_error`, `last_delivery_error`.
4. **A boot_id-deduped job needs the marker neutralized to really execute**: write a value that is NOT the
   current boot_id into the marker file (write it with the file tool — deleting the marker trips the
   destructive-command interception). The script rewrites the current boot_id at the end, so nothing needs
   restoring; but a mid-run failure leaves the stale value and the next tick then re-runs and delivers a
   duplicate — re-check the marker after the run.
5. **A check's "no anomalies" verdict covers only the instant it sampled.** A report reading "cron delivery
   8/8 normal" was followed seconds later by a dispatch failure. When you cite a report as evidence of health,
   name its sampling window or re-sample.

## Pitfalls

- **A probe-side `ImportError`/`NameError` is not evidence about the server.** Report it as a probe bug
  and re-run; concluding "server X is broken" from your own client code sends the next session hunting a
  fault that does not exist.
- **Never print the spec's `headers`/`env` values.** Build the payload into a variable, assert the mask
  holds, then print. Mask by **shape** (`Bearer <token>`, `sk-…`, `mk-…`, bare 32-char alnum) *in addition
  to* replacing the literal values you read — a mask written for one shape passes the others, and
  display-layer redaction in `read_file`/`search_files` covers only some of them.
- **An endpoint that answers but exposes zero tools is its own finding** (wrong path, auth tier, a server
  that registered nothing) — say that, rather than filing it as a handshake failure.
- **Selecting a PID by pattern picks the wrong process.** `pgrep -f <script> | head -1` returns the *shell
  wrapper* of your own command (its cgroup/PPID are the caller's), so a correct daemon looks like it sits
  inside the caller's cgroup — and a judgement built on that inverts. Select by `comm` instead:
  `ps -eo pid,comm,args | awk '$2=="python3" && /<script>/ {print $1; exit}'`, then read
  `/proc/<pid>/cgroup`. Print the two cgroups side by side (subject vs caller) before drawing any conclusion.
