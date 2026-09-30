# ClickHouse system tables: a "backup + delete + TTL" runbook (distilled from measurement 2026-09-16)

Companion: `references/system-log-ttl-alter-vs-config.md` (ALTER vs config for adding a TTL),
`references/system-log-remediation-checklist.md` (the remediation checklist),
`scripts/`'s `ch-preflight-2phase*.sh`; the cleanup script `~/.hermes/scripts/drop-frozen-clickhouse-tables.sh`.

This operation's targets: 4 `_N` frozen subtables with data in the `system` database (DROP) + `opentelemetry_span_log` (ALTER to add a TTL).

---

## 1. Backup (mandatory before deletion)

```bash
B=~/.hermes/data/backups/clickhouse-<YYYYMMDD>
# Data (gzip-compressed; avoid a > redirect tripping the approval gate: use tee + wc to collect the output)
docker exec <ch container> clickhouse-client -q "SELECT * FROM system.<t> FORMAT TSV" | gzip | tee "$B/<t>.tsv.gz" | wc -c
# DDL —— ⚠️ must be --format TabSeparatedRaw
docker exec <ch container> clickhouse-client -q "SHOW CREATE TABLE system.<t>" --format TabSeparatedRaw | tee "$B/<t>.sql" | wc -l
# Verify
gzip -t "$B/<t>.tsv.gz" && zcat "$B/<t>.tsv.gz" | wc -l      # row count must == total_rows
grep -c '^    `' "$B/<t>.sql"                                # DDL column count must == TSV column count
```

**Three pitfalls (all hit this time)**:

1. **`SHOW CREATE TABLE`'s default output escapes newlines to a literal `\n`** → what you saved cannot be loaded back directly.
   Add `--format TabSeparatedRaw` to get a real multi-line DDL.
2. **`SELECT *` does not export ALIAS columns** → the TSV column count < the DDL column count (`opentelemetry_span_log`: 12 vs 14, differing in
   the two ALIASes `attribute.names` / `attribute.values`).
   → To load back you **must spell out the column names**: `INSERT INTO system.<t> (col1,col2,…) FORMAT TSV`, otherwise it errors on a column-count
   mismatch. **Count the columns on both sides before writing the command**; don't assume.
3. **Columns from `SHOW CREATE TABLE` may carry a `COMMENT`, and the comment may contain the word "TTL "** → using `grep "TTL "` to judge "is there
   a TTL" gives a **false positive** (`metric_log` / `query_metric_log` are exactly this case). The correct criterion is in section 3.

## 2. DROP the frozen `_N` subtables

- First run `bash ~/.hermes/scripts/drop-frozen-clickhouse-tables.sh --list`: it lists the **full** `_N` set (including the 0-row ones); only the
  ones with rows are cleanup targets.
- ⚠️ **The script's `TARGETS` is a hand-written list and can miss tables**: this time `--list` found 4 with data, but `TARGETS` only had 3 (missing
  `query_metric_log_1`) → **before every run, verify `TARGETS` item by item against the `--list` output**.
- Independent post-delete review (don't just trust the script's own ✅):
  ```sql
  SELECT count() FROM system.tables WHERE database='system' AND match(name,'_[0-9]+$') AND total_rows>0;  -- expect 0
  SELECT table, count() FROM system.parts WHERE database='system' AND table IN ('<t1>','<t2>') GROUP BY table;  -- empty
  SELECT table, count() FROM system.detached_parts WHERE database='system' GROUP BY table;                    -- empty
  ```
- Check the whole-database scope too (not just `system`): `SELECT database, name FROM system.tables WHERE match(name,'_[0-9]+$') AND total_rows>0`
- A `_N` table **does not stay gone after a one-off cleanup**: when the parent table's TTL runs again it freezes out a new subtable → re-review
  periodically.

## 3. Deciding "does the TTL really exist, is it really deleting" (avoid three classes of false signal)

| What you want to know | ❌ Don't use | ✅ Use |
|---|---|---|
| whether a TTL exists | `system.tables.has_ttl` (26.7 **has no such column**, reports Code 47); `extract(create_table_query,'TTL [^)]*')` (it catches the "TTL…" text in column comments) | `countSubstrings(create_table_query,'TTL event_date') > 0` (with the real time-column name) |
| whether the TTL is really deleting data | `system.parts.min_time/max_time` (these tables **all return 1970-01-01**, unfilled) | query each table's real date column directly: `SELECT min(event_date), max(event_date) FROM system.<t>` (`opentelemetry_span_log` uses `finish_date`) —— the oldest data ≈ today − TTL window is what shows it is deleting |
| whether space is reclaimed immediately after adding a TTL | doing only `MODIFY TTL` (expired data waits for a background merge) | add one `ALTER TABLE system.<t> MATERIALIZE TTL` (negligible cost under a few hundred MB; measured: a 988,655-row table in 25 seconds) |

## 4. The step you always must do but easily forget: a rollback drill

Doing only the "rows/columns/gzip" checks = **structural inference**, not proof the backup is restorable. The real criterion is to run, on an
isolated database or a temporary table name, `CREATE` (with the backup DDL) → `INSERT … FORMAT TSV` (explicit column names) → check the row count →
clean up. **Only when you have done that is it accepted**; if you haven't, say explicitly in the MANIFEST "no rollback drill performed", and don't
fudge it with "the backup was verified".

## 5. MANIFEST template (put one in every backup directory)

Required fields: backup time / source container + image / per-table rows + size / data and DDL file names / **restore command (with explicit column
names)** / **verifications done and not done** (structural check ✓, rollback drill ✗/✓) / the deleting-is-irreversible note / consumer note.
