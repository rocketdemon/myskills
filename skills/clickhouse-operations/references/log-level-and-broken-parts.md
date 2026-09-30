# Log-level downgrade and locating corrupted parts in system log tables (measured 2026-09-13, ClickHouse 26.7.1)

Source: the user's complete diagnostic chain in the "wrap up the logger.level downgrade step" task. Every conclusion
has physical evidence (`system.server_settings` / the image's config.xml / `system.parts` / `CHECK TABLE` / a
per-minute error timeline).

---

## 1. ⚠️ Setting `logger.level` alone is **not enough** — text_log is a separate switch

This is the easiest place to waste a step. **The rows in the `system.text_log` table are decided solely by
`<text_log><level>`.**

| Setting | Default | What it affects | Hot-changeable |
|---|---|---|---|
| `logger.level` | trace | the server log file `clickhouse-server.log` | **No** |
| `<text_log><level>` | trace | the **`system.text_log` table** | No |

### Criterion: don't be fooled by `changed=1`

```sql
SELECT name, value, default, changed, changeable_without_restart
FROM system.server_settings WHERE name LIKE 'logger%';
```

Measured output (official Docker image):

```
logger.level   trace  trace  1  No
logger.size    1000M  100M   1  No
logger.count   10     1      1  No
```

**`changed` means "explicitly specified in config.xml", not "value ≠ default"** — `logger.level` has
`value == default == trace` yet `changed=1`, because the official image's config.xml (around line 24) **hard-codes**
`<level>trace</level>`, and the `<text_log>` section (around line 1338) also explicitly writes `<level>trace</level>`.

→ Seeing `changed=1` is no reason to infer "someone edited the config"; compare `value` against `default` before judging.

### How to write it (three hard constraints)

1. **Write both sections**: `<logger><level>` governs the server log file, `<text_log><level>` governs the table.
   Changing only the former = no change at all.
2. **Put them in the same config.d file**: `<text_log>` is a root element with the same name, so splitting it across
   two files can make them overwrite each other and knock out the existing TTL config. Keeping it in the same file as
   the TTL is the safest arrangement.
3. **A container restart is required**: `changeable_without_restart = No`, and `SYSTEM RELOAD CONFIG` is not enough.

```xml
<!-- Example: USER_HOME/langfuse/clickhouse-system-ttl.xml (already bind-mounted into config.d/), same file as the TTL -->
<clickhouse>
    <logger>
        <level>warning</level>
    </logger>
    <text_log>
        <level>warning</level>   <!-- ⚠️ without this line, the text_log table still collects rows at trace level -->
    </text_log>
    <!-- …the pre-existing text_log/trace_log/metric_log TTL sections… -->
</clickhouse>
```

**The actual mount on this host** (check it with `docker inspect <container> --format '{{range .Mounts}}…'`):

```
USER_HOME/langfuse/clickhouse-system-ttl.xml -> /etc/clickhouse-server/config.d/system-log-ttl.xml
```

→ **Just edit that file on the host; there is no need to change docker-compose.yml** (no new mount required).
Afterwards run `docker restart langfuse-clickhouse-1`.

### Magnitude of the effect (measured, sampled 17 minutes after boot)

```
text_log by level: Debug 34328 / Trace 17863 / Information 246  ← 97.7%
                   Warning 14 / Error 1192                      ← 2.3%
```

Dropping to warning ⇒ text_log write volume cut by **~98%**.

### `trace_log` is **unrelated** to logger.level

It is decided by _query-level_ profiler settings; measured composition Real 30% / Memory 33% / MemoryPeak 33%:

- `query_profiler_real_time_period_ns` / `query_profiler_cpu_time_period_ns` (both default 1s)
- `total_memory_profiler_step` (default 4 MiB)

To shrink trace_log you must tune these separately; changing logger.level has zero effect on it.

---

## 2. ⚠️ A checksum error gives you the **table UUID**, not the table name

```
Exception while executing background task {59a4a3af-7a56-471b-891b-23a485e671b2::202609_113983_114930_146}:
Code: 40. DB::Exception: Checksum doesn't match: corrupted data ...
```

The part before the braces is the **table's UUID**, and the part inside is the failing merge task name (the part
range). **Don't guess from the table name** — look it up first:

```sql
SELECT name, uuid, total_rows FROM system.tables
WHERE database='system' AND name LIKE '%log%' ORDER BY name;
-- 59a4a3af-7a56-471b-891b-23a485e671b2 = metric_log (in this case)
```

Then use `CHECK TABLE` to name the corrupted part precisely (more reliable than digging the part name out of logs
with a regex):

```sql
CHECK TABLE system.metric_log;
-- per-part output: result=1 is normal
-- result=0 → Code: 40 ... Checksum mismatch for file data.bin in data part <part>
--            (c96fab04… vs b803bde3…)  ← the two checksums in parentheses are the evidence
```

## 3. Distinguish "a one-off at startup" from "persistent on-disk corruption" — check whether the timeline persists across a shutdown

The first boot after a version upgrade reports a batch of checksum errors (stricter validation finding old
corruption); that is one-off. **The criterion for persistent corruption is a continuous per-minute timeline that
looked the same before the shutdown**:

```sql
SELECT toStartOfMinute(event_time) m, count() FROM system.text_log
WHERE level='Error' AND message LIKE '%Checksum%' GROUP BY m ORDER BY m DESC LIMIT 40;
```

Measured (this host's metric_log case): 84–94 errors per minute before the 09-12 shutdown and 70–86 per minute after
the 09-13 boot — **persisting across the shutdown and the reboot** — with `system.merges` recurring periodically and
CPU burned to ~67% by merge retries (visible in `docker stats`). This does not heal itself.

**Direction of the fix**: the table is purely diagnostic (it has a 14-day TTL), so
`ALTER TABLE system.metric_log DROP PARTITION '<YYYYMM>'` or `TRUNCATE TABLE` is enough. Afterwards check three
things: the count of `level='Error'` checksum rows goes to zero, `system.merges` is empty, and container CPU falls back.

## 4. Suggested order of execution (including destructive operations)

1. Read-only forensics first (every SQL in this document is read-only)
2. Changing the config (the file already mounted on the host) + restarting the container are destructive operations →
   **lay out the commands and their impact for the user before acting**
3. Verify after the restart: both `logger.level` in `system.server_settings` and the level text_log actually writes
   to disk have changed
4. Then handle the corrupted part (independent of step 2; can be done in a separate window)

## Related documents

- a host-side reference (`clickhouse-system-table-bloat.md`) — the full inventory of system tables, TTL config,
  measured rates, and the evidence chain for this case (the host-side record of the same conclusions)
- a host-side reference (`clickhouse-async-metric-sampling.md`) — `asynchronous_metrics_update_period_s=60`
- `references/corrupted-parts-repair.md` — the 2026-08 `_N` subtable partition cleanup procedure
