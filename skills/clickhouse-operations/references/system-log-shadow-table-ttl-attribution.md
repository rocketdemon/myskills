# System log tables' `_N` shadow tables and the "TTL attribution" verdict (settled 2026-09-19)

## Trigger scenario

A health-check script reported two seemingly contradictory results:

1. It found a frozen `_N` subtable **with rows**: `system.opentelemetry_span_log_0`
2. A system table with data **had no TTL detected**: `opentelemetry_span_log` (the active table)

Yet the remediation record of 2026-09-16 said "the TTL has entered `opentelemetry_span_log`'s metadata
(`countSubstrings(create_table_query,'TTL ')=1`)" — **that verification was a false success**.

## Physical evidence measured (2026-09-19 12:22–12:30)

| Object | Rows / size | parts | Date range | TTL in `engine_full` | Metadata `.sql` mtime |
|---|---|---|---|---|---|
| `opentelemetry_span_log` (active) | 7,698 / 260 KiB | 3 | **all 2026-09-19** | **none** | **09-19 11:41** (after the container started at 11:35) |
| `opentelemetry_span_log_0` | 231,166 / **7.39 MiB** | 4 | 2026-09-08 → 09-18 | **has** `TTL finish_date + toIntervalDay(14)` | **09-16 15:16** (= the moment the ALTER ran that day) |

- Total 238,864 rows / ~7.65 MiB; versus after the 09-16 remediation (218,204 rows / 7.03 MiB) ⇒ **it did not balloon back to 33 MiB**
- `system.part_log`: `_0` had 4 `RemovePart` events that day at 11:47 ⇒ **the TTL really is deleting data on `_0`**
- The container is `healthy` (started 09-19 11:35 CST); the `system` database still has **16** tables with data (consistent with the baseline)
- **The key physical evidence for attribution is the mtime of the two `.sql` files**: whichever was ALTERed at 09-16 15:16 is the one that
  still goes by `_0` now

## Conclusion

- The 09-16 verification query was `SELECT countSubstrings(create_table_query,'TTL ')>0 FROM system.tables
  WHERE database='system'` — **with no name filter**, so the two tables matched once in total, and what it read was the **shadow table**.
  A classic "wrong measurement scope" false success (the same family as SOUL's "verification covers only the known space")
- ⇒ **The active table currently has no TTL**; ch-3 is not closed; the TTL sits on the `_N` sibling table with 231k rows
- ✅ **Settled (2026-09-20, source-level, see the "Mechanism verdict" section at the end)**: the true cause of the TTL landing on `_N` = **the
  boot-time rotation mechanism** (the existing CREATE ≠ the config-generated CREATE → the old table is renamed to `_N` and a new one is rebuilt
  from the config). The original candidate explanation pointed in the right direction, but **the trigger condition is "definition mismatch", not
  "a restart"**; the true cause of "can't find it in the logs" was also found: the rotation message is **`LOG_DEBUG`**, and this host's
  `logger.level=warning` suppressed it (and the internal RENAME does not enter `system.query_log`).

## Reusable attribution recipe

```sql
-- 1) The whole same-name family: the active table + all _N, each with its rows/date span/has-TTL
SELECT table, sum(rows) AS rows, count() AS parts,
       min(min_date) AS oldest, max(max_date) AS newest
FROM system.parts
WHERE active AND database='system' AND table LIKE 'opentelemetry_span_log%'
GROUP BY table ORDER BY table;

SELECT name, metadata_modification_time, engine, engine_full
FROM system.tables
WHERE database='system' AND name LIKE 'opentelemetry_span_log%' FORMAT Vertical;
```

```bash
# 2) Attribution: the in-container metadata .sql mtime (who was changed, and when) + the declaration body
docker exec <ch> ls -la --time-style=long-iso /var/lib/clickhouse/metadata/system/ | grep -i opentelemetry
docker exec <ch> head -c 400 /var/lib/clickhouse/metadata/system/opentelemetry_span_log.sql    # ATTACH TABLE _ UUID '...'
docker exec <ch> head -c 400 /var/lib/clickhouse/metadata/system/opentelemetry_span_log_0.sql
```

```sql
-- 3) Data flow / cleanup behaviour (who deletes, and which table)
SELECT event_type, table, count() AS n, min(event_time) AS first, max(event_time) AS last
FROM system.part_log
WHERE table LIKE 'opentelemetry_span_log%' AND event_time > now() - INTERVAL 4 DAY
GROUP BY event_type, table ORDER BY last DESC;
```

## Rules when writing health checks / reports

1. **After adding a TTL to a system log table, verification must name the "active table" explicitly** (`AND name='<base table name>'`),
   and **look at its `_N` siblings at the same time**; a "has TTL" check without a name filter very likely came from a shadow table.
2. Before reporting "all system tables have a TTL", first enumerate the tables with data in the same-name family — **a `_N` with data must be
   reported separately** and must not be hidden behind the active table's ✅.
3. `system.parts.min_date/max_date` can only judge **that one table's** date span; cross-table readings must not be mixed into
   "the TTL window is not advancing".
4. Before acting, first answer "will adding a TTL create another `_1`" — this host's ch-4 precedent is that after remediation 4 `_N` tables
   **with data** appeared (`background_schedule_pool_log_0` / `error_log_0` / `query_metric_log_0` / `_1`), i.e. "clean up one subtable → a few
   days later a new one grows" is this mechanism's normal behaviour, not a failed cleanup.
5. **Don't treat a `_N` with rows as a fault outright**: it may be the shape of a TTL working (this time `_0` was still doing `RemovePart` that
   day). The criterion is "is the total volume under control" + "on whom does the TTL take effect", not "is `_N` zero".

## Mechanism verdict: definition mismatch → rotation at startup (2026-09-20, source-level)

**Rule (read from source, not inferred)**: this host's `SELECT version()` = `26.7.1.1315`, corresponding to
`src/Interpreters/SystemLog.cpp` → `SystemLog<LogElement>::prepareTable()`:

```cpp
if (old_create_query != create_query)          // existing table's CREATE  vs  config-generated CREATE
{
    /// Rename the existing table.
    int suffix = 0;
    while (isTableExist({db, name + "_" + toString(suffix)})) ++suffix;   // take the first free N
    ... RENAME  db.name  TO  db.name_<suffix> ...
    LOG_DEBUG("Existing table {} for system log has obsolete or different structure. Renaming it to {}.\nOld: {}\nNew: {}\n.")
    ... InterpreterRenameQuery(...).execute();
    /// Mark the old (renamed) table as readonly if it's a non-replicated MergeTree without a TTL.
}
```

⇒ **At every startup, each system log table is compared as the string "existing CREATE == config-generated CREATE"; on a mismatch the old table
is renamed `<name>_<first free N>` and a new one is built from the config. The trigger condition is a definition mismatch, not the restart itself.**
**The config is the sole authority at startup** —— any `ALTER` (adding a TTL / changing the structure) is "rolled back" this way at the next startup.

**Three natural experiments on this host (two positive groups + one control group, all re-runnable)**

| Time | Event | Observed result |
|---|---|---|
| 09-13 18:42:51 | config.d adds `<ttl>` to `error_log` / `background_schedule_pool_log` / `query_metric_log`, then the container is restarted | the three **active tables were rebuilt** (`.sql` mtime = 09-13 18:43:28 / 18:43:29 / 18:46:50; all `has_ttl=1`); the old no-TTL versions were left as `_N` (the 4 that ch-4 dropped on 09-16) ⇒ **a config-side TTL genuinely takes effect** |
| 09-16 15:16 | `ALTER MODIFY TTL` on `opentelemetry_span_log` (config side: that table has **no TTL at all**) | definition ≠ config ⇒ rotation at the 09-19 11:35 container start: the old table (with a TTL) → `_0` (its `.sql` mtime frozen at 09-16 15:16), and the **newly created active table has no TTL** (`.sql` mtime 09-19 11:41:49) ⇒ **the ALTER is overwritten by the config** |
| 09-20 16:01 | another container start (the active table is now the config-built one, already matching) | **no rotation**: `_1` does not exist, the active table's `.sql` mtime is still 09-19 11:41:49 ⇒ **the control group proves "only a mismatch rotates"** |

**Why the previous time (09-19) the log could not be found**: the rotation message is `LOG_DEBUG`, and config.d sets `logger.level` to `warning`
⇒ it was suppressed; the internal RENAME also does not enter `system.query_log` (measured: `query LIKE 'RENAME%'` matched 0).
⇒ "it's not in the log" ≠ "it didn't happen" — such mechanism-level events must be judged from **structural/metadata physical evidence**.

**Easy misjudgement (hit and corrected this time)**: `create_table_query LIKE '%readonly%'` is matched by **column names** by mistake
(`system.settings` has a `readonly` column; `replicas`/`database_replicas` have `is_readonly`; `metric_log`'s ZooKeeper-related columns have
`readonly_start_time`/`readonly_duration`) — the tables on this host that genuinely carry `table_readonly` = **0**.
Source comment: a rotated-out old table is marked `table_readonly` **only if it has no TTL** (saving CPU; it doesn't even do TTL reclamation);
one with a TTL stays writable. This host's config.d gives a TTL to almost all logs ⇒ there are currently no readonly tables.

**A direct corollary for the remediation plan (overturns the original "option ①")**

- Doing only `ALTER MODIFY TTL` **is not "risky", it is "ineffective"**: at the next container start the ALTERed table is renamed to `_1`, and
  the active table returns to the no-TTL state (already measured once, 09-16→09-19).
- **The real fix = write the TTL inside config.d's `<engine>` string** (that table's image carries an `<engine>`; a separate `<ttl>` element
  crashes with Code 36 —— the v1 lesson). The shape aligns with `_0`'s CREATE:

  ```xml
  <opentelemetry_span_log>
      <engine>
          engine MergeTree
          partition by toYYYYMM(finish_date)
          order by (finish_date, finish_time_us)
          ttl finish_date + toIntervalDay(14)
      </engine>
  </opentelemetry_span_log>
  ```

- **The effect path**: edit config.d → restart the container → this mechanism automatically does "rename the old active table to `_1` + build a new
  TTL-carrying one from the config", **with no ALTER and no manual table creation**; `_1` (no TTL) is marked `table_readonly` (no reclamation) and
  must be dropped manually when needed. This is exactly the path the three tables of 09-13 took.
- **Precondition**: a two-phase rehearsal (`references/config-change-preflight.md`) —— v1 once crashed 32 times / 3.5 minutes in a Code 36 loop.
- **Acceptance criteria (after the change)**: ① the active table `has_ttl=1` and `AND name='opentelemetry_span_log'` ② `_1` appears and is marked
  `table_readonly` ③ restarting once more does **not** produce `_2` (the definition is now stable).

## Production execution result (2026-09-20 18:30, the config route landed a 7-day TTL)

**Artifact**: the script `~/.hermes/scripts/ch-apply-ttl-7d.sh` (md5 precheck → stop dependents → `docker restart` → health check 40×5s
→ **auto-rollback on failure** → `SYSTEM FLUSH LOGS` → print the rotation result → bring the dependents back, teeing everything to `apply.log`);
backup and full logs in `~/.hermes/data/backups/ch-ttl-20260920_175617/`.

**Result (independently reviewed, not the script's self-report)**

| Table | Rows | has_ttl | `TTL ... +7d` | `table_readonly` |
|---|---|---|---|---|
| `opentelemetry_span_log` (new active) | 0 → 395 (after the probe) | **1** | **1** | 0 |
| `opentelemetry_span_log_0` (old shadow, self-draining) | 231,166 | 1 | 0 (it is 14d) | 0 |
| `opentelemetry_span_log_1` (the old active table rotated out this time) | 67,513 | 0 | 0 | **1** |

### Four new findings (all hit or nearly misjudged this time)

1. ⚠️ **Table creation/rotation happens on the "first flush", not at the moment the process starts** —— in rehearsal, 30 seconds after Phase B came
   up the table was still in its old shape; it rotated only after forcing one `SYSTEM FLUSH LOGS`. **A production acceptance that "queries right
   after the restart and declares failure if unchanged" would misjudge**. This also explains the 6-minute gap in production on 09-19 ("the container
   starts at 11:35, the table changes only at 11:41").
   ⇒ Fix the acceptance flow as: restart → health check → **`SYSTEM FLUSH LOGS`** → then query.
2. **The measured rendering of `table_readonly`**: `SETTINGS index_granularity = 8192, table_readonly = true`
   (visible in both the metadata `.sql` and `create_table_query`). **`system.table_settings` does not exist in 26.7** (Code 60);
   use `countSubstrings(create_table_query,'table_readonly')` as the criterion.
3. ⚠️ **The inode trap of a single-file bind mount** (nearly caused a "silent no-op"):
   `patch` / `write_file` are **whole-file replacements → the inode changes** (measured 20468 → 13306), while the container **still reads the old
   inode** (the host md5 changed, the in-container md5 did not).
   `docker restart` **re-resolves** the bind-mount path (a one-shot-container experiment proved it: after replacement without a restart = old value;
   after a restart = new value and the inode is already the new one)
   ⇒ Conclusion: after editing you must **check the in-container md5**, and rely on the restart to take effect; **do not** assume "editing the file
   took effect".
4. ⚠️ **Criterion bug: a false alarm on an empty table** —— when the active table has 0 rows, `min(finish_date)`/`max(finish_date)` return
   `1970-01-01`, and the health-check script's "oldest data earlier than today−17 → TTL window not advancing" **misreports** (measured once this
   time; it disappeared automatically as data flowed in).
   Suggested fix: add a precondition to the criterion: **only judge window advance when rows > 0**.

### Stability wrap-up (the 2026-09-22 natural-reboot acceptance) —— first ask "was any table touched", then count `_N`

Acceptance criterion ③ above ("restarting again does not produce `_2`") is met: after the **09-22 09:47 natural reboot** (a host reboot bringing the
container up), the full `_N` set is still **10** and there is no `_2`; the active `opentelemetry_span_log` still carries a TTL (108,794 rows,
finish_date 2026-09-20 → 09-22); the boot report has **1** alarm left (only `_0` has data, and its 14-day window drains by itself).

⚠️ **A stronger criterion (recommended for fixed use, replacing "count the `_N` tables")**: the count can be masked by a DROP —— if a boot both
rotated a new table and (manually/by script) cleared one old `_N`, the count is unchanged and you can't see that a rotation happened. Directly ask
"was any table created/renamed at this startup":

```sql
SELECT name, metadata_modification_time FROM system.tables
WHERE database='system' AND toDate(metadata_modification_time) >= '<that boot date>';
-- empty result ⇒ no system table was created/rotated at this startup (all definitions match the config)
```

The 2026-09-22 measurement of this query **returned empty**, corroborating "no `_2`" ⇒ when judging "the definition is stable", use this as the
primary criterion. (The same generalizes to any "did a structural change happen after a restart" scenario: querying `metadata_modification_time` is
more reliable than counting objects.)

📌 The empty-table false-alarm criterion bug in item 4 of the "Four new findings" above **is still unfixed as of 2026-09-22**
(`check-ch-system-tables.sh`'s window-advance criterion lacks the "only when rows>0" precondition); its status and pending decision are recorded in
`~/.hermes/data/pending-todos.md`.

### Two things pinned down along the way

- **The config.d section is "deep-merged key by key"** (not a whole-section replacement): the new active table's DDL is **byte-for-byte identical**
  to before the change, just one extra TTL line (`diff` output is exactly `20a21 > TTL finish_date + toIntervalDay(7)`) ⇒ keys such as
  `database`/`table`/`flush_interval` were not lost.
- **Who produces the span**: `attribute` carries `http.method`/`http.referer`/`db.statement`/`clickhouse.query_id`; take the query_id back to
  `system.query_log` → `interface=2` (HTTP), user=`clickhouse`, and the SQL is Langfuse's
  `SELECT * FROM observations WHERE project_id='my-project'` ⇒ **Langfuse's HTTP queries to CH are being traced**.
  Server side `opentelemetry_start_trace_probability=0` (unchanged) ⇒ triggering relies on a **request-level OTel context** (this link is inferred,
  not directly verified).
  That table has **no external consumers** (in the last 3 days of query_log, only this host's health-check script and manual queries read it).

### Rehearsal improvements (stricter than the original playbook)

- Phase A **must force the target table into existence first** (a fresh data directory **does not** auto-create `opentelemetry_span_log`; measured
  and confirmed): run a `SELECT count() FROM numbers(3000000) SETTINGS opentelemetry_start_trace_probability=1` + `SYSTEM FLUSH LOGS`
  → this goes through the **natural-creation** path; then assert "the rehearsal instance's DDL is byte-for-byte identical to the production active
  table's DDL" (this time 22 lines identical).
- Add two assertions to Phase B: **the new active table is still named the original table name**, and **the DDL differs from before the change only
  by the TTL line**; then add Phase C: **restart once more with the same config without producing a `_2`** (to prevent a rotation loop).

## Related

- `references/system-log-ttl-alter-vs-config.md` (the trade-off between ALTER MODIFY TTL and config `<ttl>`)
- `references/frozen-ttl-subtable-cleanup.md` (the `--list` → backup → DROP cleanup flow)
- Health-check script: `~/.hermes/scripts/check-ch-system-tables.sh` (criteria ① a `_N` with data ② the active table's rows/window
  ③ TTL coverage of system tables with data), called by the boot check since 2026-09-19 (`CH_MODE=summary`)
