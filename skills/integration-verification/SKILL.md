---
name: integration-verification
description: Verify an integration after a component is wired up — a correct-looking config is not proof that it works. Trigger after configuring any cross-component integration (API key, tracing, database connection, MCP server).
trigger: After configuring Honcho Langfuse/observability, an API key, an MCP server, or any other cross-component integration
---

# Integration Verification

## Core Principle

**A complete config file ≠ a working integration.** Always verify with a real API call; never infer from the config file.

## Checklist

After configuring any integration, walk through every item:

```
□ Verify authentication with a real API call (curl -u key:secret)
□ Check the target system for new data (trace/log/record)
□ Check the source system logs for errors (journalctl / docker logs)
□ On a silent failure (no error but no data), suspect a truncated key value first
```

## Known Pitfalls

### 1. API key polluted by the display mask

**Symptom**: The key in .env / the config file looks complete (`sk-lf-...a1b2`), but authentication fails silently, the source system logs nothing, and the target system has no trace.

**Root cause**: When the key is extracted from terminal output or a temp file, the display mask (e.g. `sk-lf-...a1b2`) is written into the config file as if it were the full value. The real key is the full-length value, but the visually truncated version got written.

**Cases**:
- Langfuse API key `sk-lf-...c3d4` (truncated) → OTLP exporter 401
- Honcho Langfuse `LANGFUSE_SECRET_KEY=sk-lf-...a1b2` (truncated) → silent failure, zero langfuse mentions in the Honcho logs

**Fix**:
```bash
# 1. Confirm the real key (from the full record captured at creation time)
# 2. Update .env
# 3. Verify with curl
curl -s "http://localhost:PORT/api/public/projects" -u "pk-xxx:sk-xxx-full"
# 4. Restart the service
```

**Prevention**: Right after writing a key, verify authentication with curl or an equivalent API call. Do not trust grep output.

### 3. Environment variable dropped by pydantic `extra="ignore"`

**Symptom**: The variable in `.env` is correct (curl proves the key itself authenticates), but the integration never takes effect: zero traces/logs in the target system, no related activity in the source system logs.

**Root cause**: If the target application's Settings class sets `extra="ignore"`, any environment variable not declared as a class field is filtered out by pydantic's `DotEnvSettingsSource` and never enters the process environment. Libraries such as the Langfuse SDK read their config from OS environment variables, cannot see the filtered variable → silent failure.

**Case**: Honcho's `AppSettings` declared only `LANGFUSE_HOST` and `LANGFUSE_PUBLIC_KEY`, not `LANGFUSE_SECRET_KEY`. `.env` held the complete SECRET_KEY, but `extra="ignore"` discarded it. The Langfuse SDK could not read it at initialization → authentication failure → zero traces and no error.

**Diagnosis**:
```bash
# 1. Search whether the Settings class declares that variable
grep -n 'LANGFUSE_SECRET_KEY\|THE_VAR' <app>/src/config.py

# 2. If it is not declared, check whether model_config has extra="ignore"
grep -B5 'class.*Settings' <app>/src/config.py | grep extra

# 3. More direct: look at the initialization error the Langfuse SDK throws
# Search the app's logs for langfuse or trace
```

**Fix**: Add the field declaration `LANGFUSE_SECRET_KEY: str | None = None` to the Settings class, then restart the service.

### 2. Restarting a background process ≠ the config taking effect

**Symptom**: .env was changed but behavior did not change.

**Root cause**: A systemd service's `EnvironmentFile` is read only at start; `systemctl reload` does not necessarily re-read .env.

**Fix**: `systemctl --user restart <service>` (a full restart, not a reload).

### 4. Verification order when diagnosing a "silent failure"

When the integration config looks correct but produces no data, investigate in this priority order (common to rare):

1. **Is the key truncated** (see Pitfall 1)
2. **Was the environment variable dropped by pydantic `extra="ignore"`** (see Pitfall 3)
3. **Did the process really load the config**: check `/proc/PID/environ`; do not trust grep on the .env file
4. **Go to the target system and look for data**: do not stop at errors in the log; query the Langfuse dashboard, the query API, etc. directly
5. **Do not trust diagnostic conclusions stored in MEMORY**: a MEMORY record can be stale (e.g. a record of "Dialectic produces no trace" while 30 traces actually exist). Always verify against live data.

**Resolved case**: The recorded issue "a uvicorn route calling Dialectic produces no trace" resolved itself once the Pitfall 1 + Pitfall 3 fixes were in. Langfuse dashboard verification showed "Dialectic Agent" 30 traces and "Minimal Deriver" 1,390 traces, all normal. The supposed "framework black box" root cause was simply pydantic `extra="ignore"` discarding `LANGFUSE_SECRET_KEY`.

### 5. **When** a config value is read: construction-time snapshot vs per-turn re-read (a new process will fool you)

**Symptom**: The config file already holds the new value, reading the config file also gives the new value, **but the running process still acts on the old value**.
The most typical form is "I changed the limit/timeout/switch, behavior did not change, and the rejection message still echoes the old value".

**Root cause**: There are two kinds of config value, and conflating them always bites:
- **Re-read per turn** (config is read on every operation/message, and config loading has mtime+size cache invalidation) → takes effect immediately, **no restart needed**
- **Snapshot at construction** (read once at process start / object construction, afterwards refreshed for content but not for parameters) → **the consuming process must be restarted**; with an in-process object cache, not even "/reset, open a new session" refreshes it

**Key pitfall**: Verifying a snapshot-type config with a **freshly started process** yields a **false positive** of "already in effect" — the new process never held the old value.
The verdict must come from the effective value in the **live process**, not from the config file and not from another new process.

**Case**: `memory.memory_char_limit` moved from 8000 to 9000:
`wc -m` + config + a new process all said 9000, yet the MemoryStore inside the running gateway (started before the config change) still hard-rejected writes at **8000**,
and the rejection text echoed `Memory at 8,279/8,000 chars` — only two mutually corroborating pieces of evidence (the denominator in the system prompt header and the rejection text echoed by the live process) settled the case.

**Diagnostic recipe (read-only, no data change)**:
1. Find an operation that **gets rejected / echoes a value** and deliberately send one with an over-limit parameter → read the live process's effective value from the error message
   (e.g. a memory write whose content necessarily exceeds the limit; the rejection text echoes `N/LIMIT`; the local script `~/.hermes/scripts/probe-memory-limit.py` tests only the new-process value and **cannot** replace live-process probing)
2. Align the timeline with `/proc/<pid>/environ` + process start time vs config file mtime: **process start earlier than the change** ⇒ the snapshot hypothesis is the strongest
3. Re-verify after the fix with **the same probe** (the live process's echo should now show the new value); do not accept "the config file is changed" as acceptance

**Prevention**: After changing any config value, first establish which kind it is (read the point of use in the code: is it read on every operation, or assigned to an instance attribute at construction?).
Snapshot-type values need one restart; record "needs restart" in the change log, otherwise the next person (including yourself) will assume the change took effect.

### 6. Single-file bind mount: the host file changed but the container still reads the old content (**the inode trap**)

**Symptom**: The host file's content/md5 has changed, yet `docker exec <c> md5sum <mount path>` **still returns the old value**.
If you then "restart the config-consuming process" or assume the change took effect → **the change silently never happens** (harder to detect than a crash).

**Root cause**: `-v /host/file.xml:/container/x.xml:ro` mounts **that inode**. Any "replace the whole file" style write
(`patch` / `write_file` / an editor's write-and-rename / `mv`) gives the host a **new inode**,
while the container still holds the old inode — so the container forever sees the old content while grep on the host looks perfectly fine.

**Test (1 line, verified working)**:
```bash
stat -c '%i' /host/file.xml                                  # host inode
docker exec <c> stat -c '%i' /container/x.xml                # inode as seen by the container — a mismatch means you are hit
docker exec <c> md5sum /container/x.xml && md5sum /host/file.xml   # the content must match too
```

**Fix (proven with a throwaway container experiment)**: **`docker restart <c>` re-resolves the bind mount path** —
after replacing the file, not restarting means the container reads the old value; after `docker restart` it reads the new value, and the inode inside the container is now the host's new inode.
(`--force-recreate` is not needed; recreating the container is only necessary when the mount path itself changed.)

**Operating discipline (follow after changing any bind-mounted config)**:
1. Edit in place to keep the inode, or `cp <new content> <target file>` (**never `mv`/delete-and-recreate**)
2. After the change, **compare the md5 inside the container** (not just the host): a mismatch = the container holds the old inode
3. Make it take effect via `docker restart`; afterwards **re-verify with a behavioral assertion** (e.g. the config value/table structure read inside the container really changed)
4. Do not accept "the host file is changed" as acceptance — that is exactly the false positive this section guards against

**Case**: Before adding TTL to ClickHouse, the config.d XML was edited: after the `patch` tool wrote it, the host md5 changed while the md5 inside the container **did not** (inode 20468 → 13306).
Anticipating this and measuring with a throwaway container that "restart re-resolves" avoided a false completion of "restarted but the change never took effect, yet judged a success".

## Verification Template

```bash
# Langfuse integration verification
curl -s "http://localhost:PORT/api/public/projects" -u "$PUBLIC_KEY:$SECRET_KEY"

# Honcho log check (langfuse-related activity should be visible)
journalctl --user -u honcho-api --no-pager -n 30 | grep -i langfuse

# Langfuse traces check
curl -s "http://localhost:PORT/api/public/traces?limit=1" -u "$PUBLIC_KEY:$SECRET_KEY"
```
