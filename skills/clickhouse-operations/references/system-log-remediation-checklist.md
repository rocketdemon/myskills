# System log remediation: a self-check checklist for "is the plan missing anything?"

Companion relationship: `log-level-and-broken-parts.md` = **the diagnostic chain** (how to locate it);
this file = **the review/remediation checklist** (how to guarantee nothing is missed before acting, and how to prove it was really fixed).

**When to use**: ① the user asks "is the plan missing anything / check it"; ② a self-check before executing system-table remediation
(clearing tables / changing levels / adding a TTL); ③ a full-coverage check when the **same problem recurs for the Nth time**
(the reason this class of problem has historically recurred is "fixing only the one table that surfaced this time").

---

## Layer 1 · Config layer (a mistake here wastes the work, or the container won't start)

- [ ] **Did you change both log levels**: `logger.level` (the server log file) ≠ `<text_log><level>` (the table);
      changing only the former = no change at all
- [ ] **Did you verify the TTL column name table by table**: `opentelemetry_span_log` has only `finish_date`, no `event_date`;
      a wrong name → invalid config → the container may **fail to start**
- [ ] **Did you write into the config.d file that is actually "mounted"**: do not create a new file — `<text_log>` is a root-level
      element of the same name, and one in each of two files may overwrite each other and knock out the existing TTL.
      Run `docker inspect` first to confirm the mount relationship
- [ ] **Is a restart required**: check `system.server_settings.changeable_without_restart`
      (`logger.level` = `No`, so `SYSTEM RELOAD CONFIG` is not enough)
- [ ] **The side-effect surface after a restart**: if config.d's `<logger>` is a **whole-section replacement** rather than a deep
      merge, it will knock out the `logger.log` / `logger.errorlog` / `logger.size` / `logger.count` that the image sets explicitly
      → **after the restart you must re-check these 4**; the defensive approach is to repeat them explicitly in config.d
- [ ] **Back up the config file first + validate the XML syntax + know the rollback** (change the two lines back and restart to roll back)
- [ ] Whether the dependents (`langfuse-web` / `langfuse-worker`) must be stopped first: their writes will error while CH restarts
      (per §1 of the upgrade procedure, the convention is to stop the dependents first)

## Layer 2 · Coverage layer (looking only at the object surfaced this time = the root of recurrence)

- [ ] **Full object inventory** (including business databases, not just system):

```sql
SELECT database, table, sum(rows) AS rows,
       formatReadableSize(sum(bytes_on_disk)) AS size, count() AS parts
FROM system.parts WHERE active
GROUP BY database, table ORDER BY sum(bytes_on_disk) DESC;
```

- [ ] **Per-table TTL presence**: use `countSubstrings(create_table_query,'TTL event_date')>0`
      (`grep "TTL .*"` and `position(...)>0` both produce false positives, see SKILL.md §2)
- [ ] **Distinguish "the TTL we configured" vs "ClickHouse's built-in TTL"**: the measured built-ins are
      `processors_profile_log` 30d / `aggregated_zookeeper_log` 30d /
      `asynchronous_insert_log` 3d / `zookeeper_connection_log` 30d → don't configure them a second time
- [ ] **The log files themselves must be counted too** (not just the tables):
      `docker exec <c> du -sh /var/log/clickhouse-server` + `ls -la` to see rotation;
      `logger.size × logger.count` is the ceiling (measured config = 1000M × 10 = a 10GB ceiling, 1.8GB actually used)
- [ ] **Does the docker json-file log have a size limit set**: `docker inspect … '{{.HostConfig.LogConfig}}'`
      (no `max-size` = unbounded; `logger.console=0` only makes it grow more slowly)
- [ ] **Frozen `_N` subtables**: only the ones "with rows" count as residue (`match(table,'_[0-9]+$') HAVING rows>0`) →
      use the existing script `~/.hermes/scripts/drop-frozen-clickhouse-tables.sh`
- [ ] **The correct way to filter "TTL should have deleted but didn't"**: first confirm the table has a **Date-type column**, then use `max_date`,
      otherwise a DateTime64 business table's `min_date/max_date` is always 1970 → a flood of false alarms

## Layer 3 · Root-cause layer ("drop it" ≠ "it won't happen again")

- [ ] **Is the corruption one-off or continuous**: does the per-minute error timeline **persist across shutdowns and restarts**
- [ ] **Have you checked `crash_log`**: it is the only crash-forensics table. Its schema has **no `exception` column**;
      use `event_time, signal, query_id, fault_access_type, trace_full, version`.
      Measured: it holds one CH SIGSEGV (crashing in the target table's merge path)
- [ ] **Have you verified the causal chain**: the **partition + time** of the historical crash vs the **partition + time** of the
      current corruption. If they differ → **you cannot say "it was caused by that crash"** (temporal correlation ≠ causation);
      list it as to-be-investigated only
- [ ] **Recurrence monitoring**: use `system.errors`' error-code counters as a baseline (**a restart zeroes them — record them before
      the change**), then verify after the fix that they no longer grow; do **not** set a TTL on `crash_log`, or the evidence is lost
- [ ] **The boundary of hardware investigation**: in WSL, `dmesg` cannot see Windows host disk errors →
      **"dmesg is clean" is not evidence of "hardware ruled out"** (absence of evidence ≠ evidence of absence)

## Layer 4 · Incidental-discovery layer (don't let anomalies outside your field of view slip away)

- [ ] Does the business database have anomalies of its own (this case: the `traces` table had 93 **future-timestamp** records,
      of which **116 are in the future**, and **1169/5614** skew by more than 1h; `name` has two families:
      `Hermes turn` (fabricates a "daily sequence", up to 18 days ahead) + `Dialectic Agent` (duplicate ingest,
      offset exactly +8h/+16h/+24h) → see open item ⑤ at the end of this document)
- [ ] Is the business table Replicated (via the container's embedded Keeper) → is the startup `KEEPER_EXCEPTION` only transient
- [ ] Who owns business-table retention (Langfuse's own 60-day job, not a CH TTL) → don't add a CH TTL by mistake
- [ ] When container CPU / load is abnormally high, is it just this corruption's merge retries burning it (this case CPU ~67%, load 14)

---

## Forensic SQL recipes (all read-only)

```sql
-- 1) Table UUID → table-name reverse lookup (checksum errors give a UUID, don't guess from the table name)
SELECT name, uuid, total_rows FROM system.tables WHERE database='system' ORDER BY name;

-- 2) Error aggregation: pin down "how many corrupted spots there are" (store prefix + merge task + error code)
SELECT count() AS errs,
       extract(message,'store/([0-9a-f]{3})/')                    AS store_prefix,
       extract(message,'::([0-9]+_[0-9]+_[0-9]+_[0-9]+)')         AS merge_task,
       extract(message,'Code: [0-9]+')                            AS code
FROM system.text_log
WHERE level='Error' AND event_time > now() - INTERVAL 6 HOUR
GROUP BY store_prefix, merge_task, code ORDER BY errs DESC LIMIT 15;

-- 3) Precisely name the corrupted part (more reliable than scraping the part name out of a log with a regex)
CHECK TABLE system.<table>;   -- each line is <part>\t<result>; 1=normal, a non-1 carries two checksum values

-- 4) Timeline of the corrupted part + the table's part count
SELECT name, active, rows, modification_time FROM system.parts
WHERE database='system' AND table='<table>' ORDER BY name DESC LIMIT 10;

-- 5) Pre-fix baseline (a restart zeroes it, be sure to record it first)
SELECT name, code, value, last_error_time FROM system.errors WHERE value>0 ORDER BY value DESC;
```

## Whole-database `CHECK TABLE` sweep (run it in the background, don't wait in the foreground)

```bash
# Use a .sql file + clickhouse-client -n to submit several statements at once (avoids a long inline command being blocked)
cat > /tmp/ch_check_all.sql <<'SQL'
CHECK TABLE system.part_log;
CHECK TABLE system.query_log;
-- … every table that has data …
CHECK TABLE default.observations;
SELECT 'SWEEP_DONE' AS marker;
SQL

docker exec -i <container> clickhouse-client -n < /tmp/ch_check_all.sql > /tmp/ch_check_result.txt 2>&1

# Verdict: each TSV line is <part>\t<result>; anything not 1 is corrupted
awk -F'\t' 'NF>1 && $2!="1"' /tmp/ch_check_result.txt
```

- Small tables (tens of MB) — 15 of them finish in about **2 minutes**; large tables (`text_log`/`trace_log` ~500MB each)
  take **>7 minutes** → you must use `background=true` + `notify_on_complete=true`; the foreground always times out
- ⚠️ **The terminal tool's timeout parameter is named `timeout` (seconds), not `timeout_s`**; getting it wrong means it is **silently
  ignored** and falls back to the 180s default → a long sweep times out immediately (hit in this session)
- After the verdict, add one more: `SELECT count() FROM system.detached_parts` (should be 0)

---

## Baseline snapshot of this host (for reference, ClickHouse 26.7.1; 2026-09-13)

| Item | Measured value |
|---|---|
| Total data | 2.1 GB, of which **system logs ~1.32 GB (~63%)** |
| System tables with data | **16**: 10 with a TTL (6 self-configured 14d + 4 CH built-in 30d/30d/3d/30d), 6 without a TTL |
| Tables without a TTL | `opentelemetry_span_log` 29.6MB / `background_schedule_pool_log` 10.7MB / `query_metric_log` 3.7MB / `query_metric_log_0` 2.3MB / `error_log` 0.9MB / `crash_log` 1 row; oldest data **2026-06-10 (≈95 days of unbounded accumulation)** |
| Log files | `clickhouse_logs` volume 1.8 GB (server.log 273MB + err.log 168MB + 10×gz ~430MB) |
| Write rate | `text_log` 2500–3300 rows/min; `trace_log` ~5800 rows/min; Error ~75/min (all corruption-part retries) |
| Corrupted object | `system.metric_log`'s `202609_113983_114708_145`; the 202609 partition is 24 parts / 265,615 rows / ~105MB |
| Error timeline | first seen **2026-09-08 18:50:33** (the part was created at 09-08 16:49), **91,062** times cumulative through 09-13 |
| crash_log | exactly 1: **2026-08-16 20:17:26 signal 11 (SIGSEGV)**, crashing in `metric_log`'s merge path (202608 partition) → **differs from the 09-08 corruption in both partition and time; causation unverified** |
| Whole-database CHECK | apart from that part, **no second corruption**; `detached_parts` is empty |
| Business side | `observations` 23,241 rows / 84MB; `traces` 5,543 rows / 34MB (93 of them future timestamps) |

## Related documents

- `references/log-level-and-broken-parts.md` —— the diagnostic chain (the two log levels, UUID reverse lookup, transient vs persistent)
- `references/corrupted-parts-repair.md` —— the 2026-08 `_N` subtable partition cleanup procedure
- `host-management/references/clickhouse-system-table-bloat.md` —— the host-side record of the same conclusions
- `host-management/references/clickhouse-async-metric-sampling.md` —— `asynchronous_metrics_update_period_s=60`

---

## Open items (to execute · recorded 2026-09-14)

> Done this round: ② clearing the log files (2.1G → **753M**, deleting 20 rotated `.gz` files, printing each byte count;
> the Code 36 evidence is archived under `~/.hermes/data/backups/ch-logclean-*`).

- [ ] **③ Add a TTL to `opentelemetry_span_log`**
  ```sql
  ALTER TABLE system.opentelemetry_span_log MODIFY TTL finish_date + toIntervalDay(14);
  -- Verify: SELECT countSubstrings(create_table_query,'TTL ') FROM system.tables
  --       WHERE database='system' AND name='opentelemetry_span_log';   -- should be 1
  -- Rollback: ALTER TABLE system.opentelemetry_span_log REMOVE TTL;
  ```
  - **No restart, no need to stop langfuse-web/worker**
  - ★Measured (a controlled two-phase rehearsal on 09-14): writing `ttl` into config.d's `<engine>` **is only effective for "newly created
    tables", not for already-existing tables** — the config was indeed loaded (`logger.level=warning`), but `create_table_query` had zero TTL
    and no `_N` subtable. See `references/system-log-ttl-alter-vs-config.md`
  - `~/.hermes/data/proposed/clickhouse-system-ttl.xml.v3-20260914` is kept as a default value "for when the volume is rebuilt";
    **there is no need to restart the container for it** (the rehearsal already proved Code 36=0 and the engine key intact)
  - Current state: 911,318 rows / 30.58 MiB / oldest 2026-06-10; the official CH table comment says
    *"It is safe to truncate or drop this table at any time."*
- [ ] **④ DROP the 4 frozen `_N` subtables** (`~/.hermes/scripts/drop-frozen-clickhouse-tables.sh`,
      refactored to "first list every `_N` → delete TARGETS → verify the residue item by item"; `--list` only lists, never deletes)
  | Table | Rows | Size |
  |---|---|---|
  | `background_schedule_pool_log_0` | 197,783 | 10.75 MiB |
  | `error_log_0` | 109,336 | 895.87 KiB |
  | `query_metric_log_0` | 18,246 | 2.32 MiB |
  | `query_metric_log_1` | 27,403 | 3.77 MiB ← **missed in the first-round list, added 09-14** |
  - All are `system`-database TTL subtables (another 12 have 0 rows), have no consumers, and **deletion is irreversible**
- [ ] **⑤ Locate the writer of the "future-timestamp trace"**
  - 3 **pure SELECTs** still to run: compare `timestamp` / `created_at` / `event_ts` column by column, and look at `metadata` /
    `tags` / `user_id` (criterion: `dateDiff('day', event_ts, timestamp) > 5`)
  - ⚠️ **A command shape with `>` redirection or writing a file trips the approval gate** (blocked twice on 09-14; pure read-only commands
    passed normally) → issue pure SELECTs only, don't write temp files; and **don't retry / don't swap the command to get around it**
  - Code locations: `hermes-agent/plugins/observability/langfuse/__init__.py:904` (`trace_name="Hermes turn"`);
    `honcho/src/llm/types.py:79` + `honcho/src/llm/tool_loop.py:73` (`agent_type="Dialectic Agent"`)
  - The plugin has **no** explicit start_time/timestamp computation → it is suspected to be written elsewhere; to be pinned down by the SELECTs
  - Data-layer handling (soft-delete `is_deleted` / correct by `event_ts`) **is decided only after the root cause is fixed**; don't touch it yet
- [ ] **⑥ Rehearsal leftover directories (need the user's sudo to delete)**: `~/.hermes/data/backups/preflight-datadir`,
      `preflight-datadir-v3`, `preflight-datadir-v3b` (owned by root; the host user has no permission to delete)
- ① `text_log` 497 MiB of existing data: **the user's position = do not TRUNCATE** (accounting only)
