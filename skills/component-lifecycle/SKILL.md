---
name: component-lifecycle
description: Use when installing or managing a new tool, component, or service — the general method and checklist. Triggers when the user wants to install a new tool, add a new service, configure a new component, or troubleshoot a component failure.
trigger: installing a new Docker container, systemd service, MCP tool, or Python package, or any long-running background component. Also load it when troubleshooting a component failure.
---

# Component Lifecycle Management

A general method distilled from real incidents: a system deadlock, a ClickHouse UUID decoupling, an MCP search tool evaluation.

---

## 1. Container lifecycle

### Rules
- **Start and stop with `compose stop` / `compose start`.**
- **Never use `compose down`** (unless you explicitly intend to discard the container and its internal metadata).
- `compose down` = delete the container + rebuild it on the next `up` → the container's internal UUID / metadata may become decoupled.

### Ask before installing a new container
```
□ Does the container's internal state depend on metadata created at creation time? (UUID, database schema, certificates, …)
□ After the container is deleted, is the data volume still there? Can the new container recognize the data inside the volume?
□ Is the image tag `latest` or a pinned version? (`latest` carries an automatic-rebuild risk)
```

### Verification
- After installing, run `docker inspect <container> --format '{{.Created}}'` and record the creation timestamp.
- Run at least 2 stop/start cycles to confirm that no data is lost.

---

## 2. systemd service management

### Rules
- **One service's ExecStart must not call `systemctl start/stop` on another service** → deadlock risk.
- Express start-order dependencies with `After=`/`Before=`/`Requires=` and let systemd schedule them itself.
- If you must orchestrate the start order → start the process directly (bypassing systemd); do not nest `systemctl` calls.

### Checks when creating a new systemd service
```
□ Has any other service declared After= or Before= on this service?
□ Does ExecStart call systemctl? (yes → change it)
□ Type=oneshot or simple? (oneshot blocks default.target; simple does not)
□ Is RemainAfterExit=yes set correctly?
```

### Diagnosing a deadlock
```bash
systemctl --user list-jobs          # look at the job queue and find jobs in `waiting`
systemctl --user list-dependencies  # draw the dependency tree
systemctl --user show <svc> -p After -p Before  # exact dependencies
```

---

### Confirm that a service exists before querying its status

`systemctl is-active` returns `inactive` even for a service that does not exist — do not equate `inactive` with "the service exists but is not running".

```bash
# Correct approach: list every installed service first
systemctl --user list-units --all --no-pager | grep <name>
# or read the unit file directly
systemctl --user cat <name>.service
```

**A service with no unit file does not exist.** Do not list it, do not report a status — just say "it does not exist".

---

## 3. Layered troubleshooting

When a component "does not work", verify bottom-up layer by layer, confirming each layer before moving up:

```
Port layer:        ss -tlnp | grep <port>        → is the port listening?
Process layer:     run the start command by hand → does the code / environment run?
Service layer:     systemctl start / journalctl  → where is systemd stuck?
Config layer:      cat config / grep key params  → is the configuration right?
Dependency layer:  docker ps / list-jobs         → are the dependency components alive?
```

Do not skip layers and jump straight to guessing the root cause.

---

## 4. Multi-point verification

A single negative result is not a conclusion.

### Rules
- A tool does not work → **confirm with at least 3 different parameter sets / scenarios**
- "Does not work" and "does not work under specific conditions" are two different things
- A positive conclusion must be verified directly against the underlying data (do not trust only the tool's output)
- **Every dependency claim must be checked item by item**: before asserting "package X is unnecessary", search the codebase with `grep -rl "import X"`. Declaring in bulk from memory that "they are all unnecessary" → the user will correct you with "do not assume"
### MCP/API tool testing template

```
Test 3 times with the same intent but different keywords
Test once with keywords from a completely different domain (to verify generality)
If it returns empty → strip the suspected special characters and test again
If it is time-related → swap in a different year/date and test again
```

See `references/mcp-search-testing-methodology.md` for details (including the measured Metaso "2025" filter matrix)

---

## 5. State snapshot

Before any operation that could break state, archive it first:

```bash
docker ps --format 'table {{.Names}}\t{{.Status}}' > /tmp/pre-ops-snapshot.txt
systemctl --user list-units --state=running >> /tmp/pre-ops-snapshot.txt
df -h >> /tmp/pre-ops-snapshot.txt
```

Afterwards, compare the snapshot with the current state to know exactly what changed.

---

## 6. New-component integration checklist

For every new component/service you install, walk through this list in order:

```
□ Where is the data? (volume / mount / DB file / config path)
□ Does the state depend on immutable metadata? (container ID / UUID / certificate fingerprint)
□ Dependency graph: who starts first, who waits for whom, what breaks when one of them goes down
□ Write into startup.sh (start) and orderly-shutdown.py (stop)
□ Run 2 full stop/start cycles
□ Confirm the systemd service has no nested systemctl calls
□ Test the break scenario: how does it fail once its dependency is shut down
□ Record the key paths and the configuration to memory
□ Update the checklist above into this skill
```

---

## 7. Known pitfalls at a glance

| Pitfall | Symptom | Cause |
|------|------|------|
| Data loss after `docker compose down` | the table is still there but the data cannot be opened | the container is rebuilt → ClickHouse UUID decoupling |
| A systemd service hangs on startup | `systemctl start` times out | ExecStart calls another `systemctl start` |
| A tool cannot find specific content | empty result | internal filtering/tokenization (e.g. how Metaso handles "2025") |
| `session_search` cannot find older sessions | only recent results come back | the dedup + sort policy favours recent results; this is not data loss |
| `systemctl is-active` reports a non-existent service | a service that does not exist is listed as "installed but not running" | `is-active` returns "inactive" for non-existent services too |
| `docker run alpine du` times out | assumed to be "too much data", actually just too many files | a ClickHouse MergeTree table has hundreds of thousands of part files and `du` walks them slowly. Use `docker exec <container> du -sh <dir>` |
| Hermes upgrade compatibility unknown | a component stops working after the upgrade | run a systematic compatibility analysis before touching anything. See the `hermes-agent` skill's `references/hermes-upgrade-compatibility-checklist.md` |
| Hermes `uv sync` does not install extras | the Gateway crashes on startup (exit 75) | `uv sync` installs only the 60 base packages. You must add `--extra messaging --extra honcho --extra feishu`. See your own Hermes maintenance checklist |
| `uv sync --extra X` keeps only X | all other extras are uninstalled (48 packages vanish) | `--extra` is a **replace** mode, not an append. To keep several extras you must **list them all at once**: `uv sync --extra messaging --extra honcho --extra feishu --extra edge-tts --extra web --extra mcp`. Or install everything with `--all-extras`. **Fixing this after the fact is dangerous** — the running Gateway process is unaffected, but a restart will crash it |
| Asserting "package X is unnecessary" without verification | the user corrects you with "do not assume" | when N packages are missing, `grep -rl` the source for each import, classify them (direct reference / lazy import / transitive / unused), and only then conclude |
| The cron `no_agent` script is not found | a `Script not found` error | a relative path is always resolved against `~/.hermes/scripts/`, regardless of `workdir`. Use a wrapper script, not a symlink (it is detected as an escape). See `references/cron-script-path-pitfall.md` |
| MCP tools fail silently | the Gateway runs fine but the MCP tools are unavailable | zero MCP entries in the log = the `mcp` package is not installed; connection errors in the log → keep diagnosing. See `references/mcp-connection-diagnostics.md` |
| ClickHouse system tables balloon | assumed to be Langfuse business data, actually ClickHouse's own logs | check table sizes first: `SELECT database, table, formatReadableSize(sum(bytes)) FROM system.parts WHERE active GROUP BY database, table ORDER BY sum(bytes) DESC`. Separate `system.*` (ClickHouse logs) from `default.*` (business data). See "ClickHouse system-log TTL configuration" below |
| Overclaiming | you are forced to walk it back after the user challenges it, which damages trust | before claiming "X is utterly useless", think through the boundary conditions first. Say instead "X has little value in scenario Y, but still has diagnostic uses in scenario Z" |\n| MCP diagnosis: a bug in the test code passed off as protocol incompatibility | `AttributeError: 'InitializeResult' object has no attribute 'protocol_version'` misjudged as "mcp 1.26.0 protocol incompatibility" | before asserting MCP protocol incompatibility, self-check the test code: ① the attribute name is correct (`result.capabilities`, not `result.protocol_version`) ② the returned object type is correct (Tool, not dict) ③ whether the exception was raised by your own code or returned by the server |\n| Giving up during diagnosis with "out of scope" | the user asks "what do you mean by that?" | every failure has an actionable next step: read the log, read the source, reproduce by hand, read the docs, search for known issues. When unsure, list executable diagnostic commands instead of declaring the case closed |
| Langfuse data retention only applies to new projects | after changing LANGFUSE_INIT_PROJECT_RETENTION, old project data is not cleaned up | new projects inherit the 60-day TTL automatically; old projects must be set manually in the Langfuse UI. After editing the compose file, restart web+worker with `docker restart` (not `compose restart`) |

## 8. ClickHouse system-log TTL configuration

### Problem

ClickHouse's system tables (`trace_log`, `text_log`, `metric_log`, `asynchronous_metric_log`, `part_log`, `query_log`) accumulate continuously and can occupy 80%+ of the disk in a single-node deployment.

### Diagnosis

```bash
docker exec <container> clickhouse-client -q "
SELECT database, table, formatReadableSize(sum(bytes)) as size, sum(rows) as rows
FROM system.parts WHERE active
GROUP BY database, table ORDER BY sum(bytes) DESC LIMIT 15"
```

- `system.trace_log` / `system.text_log` are the big consumers (a single table can reach 1-2GB)
- `default.traces` / `default.observations` are the business data (usually tens of MB)

### Setting the TTL (config-file approach)

1. Create an XML config file (e.g. `clickhouse-system-ttl.xml`) and mount it into `/etc/clickhouse-server/config.d/`
2. Keep `text_log` for 60 days (it has diagnostic value) and the rest for 30 days
3. Restart ClickHouse for the change to take effect

### Caveats

- **The TTL only applies at table-creation time**: existing old data is not bound by the new TTL and needs separate cleanup
- **Do not casually declare system logs "useless"**: `text_log` has diagnostic value; `trace_log` has little value in a single-node deployment
- **Check the table sizes before choosing a policy**: do not assume the growth is business data
