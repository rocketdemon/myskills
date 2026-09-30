# Adding a TTL to system log tables: the trade-off between `ALTER` and `config.d` (controlled measurement 2026-09-14)

Companion: `SKILL.md` §2 (TTL config / the `<engine>` table), `references/system-log-remediation-checklist.md`.
This document corrects SKILL.md's older advice "don't bother configuring a TTL for a table with an `<engine>`" —— **that table can safely take a
TTL, it just cannot go through the config route**.

---

## One-line conclusion

| The table's current state | Correct approach | Why |
|---|---|---|
| **The table already exists** | `ALTER TABLE system.<t> MODIFY TTL <date_col> + toIntervalDay(N)` | config.d's `<engine>` is used **only at table-creation time**; an existing table is not diffed/ALTERed |
| The table does not exist yet / the volume will be cleared and rebuilt | only then consider config (splice the ttl into the `<engine>` string, or a separate `<ttl>` element) | only the creation path reads the config's engine |

**And ALTER completely sidesteps the whole class of Code 36 crash-loop risk** (no config edit, no container restart).

---

## Controlled experiment (reproducible)

Purpose: determine whether "splicing the ttl into the `<engine>` string" has any effect on an **already-existing** table.

```
Phase A  a one-shot container (mounting a temporary data directory, not our config)
         manually create system.opentelemetry_span_log using the 【production DDL】 (no TTL):
           docker exec -i <pre> clickhouse-client -n <<< "$(SELECT create_table_query FROM system.tables …)"
Phase B  the same data directory + config.d mounted (that section contains <engine> … ttl finish_date + toIntervalDay(14)) restart
```

Criteria and results:

| Observation point | Result | Reading |
|---|---|---|
| `SELECT value FROM system.server_settings WHERE name='logger.level'` | `warning` | **the config was indeed loaded** (otherwise you can't distinguish "the config didn't take effect" from "it took effect but didn't reach the target") |
| `countSubstrings(create_table_query,'TTL ')` | **0** | the engine-embedded ttl **did not land on the table** |
| `extract(create_table_query,'ENGINE = .*')` | **byte-for-byte identical** to before the config was mounted | CH did no diff/ALTER at all |
| whether `opentelemetry_span_log_0` exists in `system.tables` | does not exist | no trace of any TTL taking effect |
| the `Code 36` count in err.log | 0 | the config is valid, no crash |

**Counter-evidence (showing the separate `<ttl>` element route is effective)**: when this host's v2 configured `background_schedule_pool_log` /
`query_metric_log` / `error_log` with **separate `<ttl>` elements**, it took effect on restart and corresponding `_0` / `_1` frozen subtables grew
in `system.tables` —— showing CH will run an `ALTER … MODIFY TTL` based on it.

---

## Correct operation and verification

```sql
-- Effective (zero restart; written into the table metadata, survives a container restart)
ALTER TABLE system.opentelemetry_span_log MODIFY TTL finish_date + toIntervalDay(14);

-- Verification 1: did the TTL enter the metadata
SELECT countSubstrings(create_table_query,'TTL ') FROM system.tables
WHERE database='system' AND name='opentelemetry_span_log';        -- expect 1

-- Verification 2 (corroborating): a TTL subtable will appear later
SELECT name, total_rows FROM system.tables
WHERE database='system' AND match(name,'opentelemetry_span_log_[0-9]+');

-- Rollback
ALTER TABLE system.opentelemetry_span_log REMOVE TTL;
```

Supplementary facts:
- The table **has no `event_date`**, only `finish_date` (a wrong column name → invalid config / an ALTER error)
- The official table comment reads *"It is safe to truncate or drop this table at any time"* →
  a one-off `TRUNCATE` is an acceptable alternative too (but TRUNCATE treats the symptom, not the cause; ALTER is the permanent fix)
- The whole table is ~911k rows / 30.6MB, growing ~18k rows/day ≈ 0.3MB/day

**✅ 2026-09-16 production execution measured (following this recipe succeeds directly, including the "immediate reclaim" step)**:

| Step | Measured |
|---|---|
| Baseline | 988,655 rows / 33.04 MiB / 8 parts; `finish_date < today()-14` = **763,702 rows**; span 2026-06-10 → 09-16 |
| Pre-delete backup (user requirement) | `~/.hermes/data/backups/clickhouse-20260916/opentelemetry_span_log.tsv.gz` (33.5MB, row count verified equal + `gzip -t` passed) + `.sql` (`--format TabSeparatedRaw`; note the default TSV output escapes newlines in the DDL to a literal `\n`, only TSVRaw can be loaded back directly) |
| `MODIFY TTL` | returns in seconds; the TTL **appears in the metadata immediately** (`countSubstrings(create_table_query,'TTL ')=1`, expression `TTL finish_date + toIntervalDay(14)`) |
| `MATERIALIZE TTL` (optional, immediate reclaim) | 25 seconds later: **218,204 rows / 7.03 MiB / 9 parts**; `system.mutations` unfinished = 0 |
| Side effects | **no Code 36, no container restart, the container stays `healthy`** —— confirming "ALTER sidesteps the whole class of config risk" |

Note: with only `MODIFY TTL`, expired data waits for a background merge to be deleted (this time the `opentelemetry_span_log_[0-9]+` subtables had
not appeared yet); to reclaim the space **immediately**, add one `MATERIALIZE TTL` (the cost is negligible when the table is under a few hundred MB).

---

## ⚠️ Two "rehearsal false-pass" blind spots (hit in the same batch of measurements)

The current version of `scripts/config-preflight-2phase.sh` has a **criterion defect**; the foreground session should fix it (see the patch at the end):

### Blind spot one: the subject under test does not exist at all in a clean environment

System log tables are **lazily created** —— `opentelemetry_span_log` **is not created** in a bare container with no spans produced.
So the assertion returns **empty**, while the old criterion only checked "was there an error" → it put a ✅ against an empty result.

```
B4. did the TTL land on an existing table → (empty)
B4c. SELECT count() FROM system.parts … → 0
B4d. … FROM system.opentelemetry_span_log → Code: 60 UNKNOWN_TABLE
```

Fix: Phase A should use the **production DDL** (`SELECT create_table_query FROM system.tables WHERE …`) to manually create the table.
This host has put the creation SQL at `~/.hermes/data/proposed/preflight-phaseA-extra.sql`,
and the new rehearsal script has a hook that reads it.

### Blind spot two: the assertion SQL itself is wrong, and the error is read as "no anomaly"

```sql
-- ✗ mixing count() with the plain column create_table_query → the whole assertion fails = as good as not testing
SELECT count() AS exists, countSubstrings(create_table_query,'TTL ')>0 AS has_ttl
FROM system.tables WHERE database='system' AND name='opentelemetry_span_log';
-- Code: 215 … Column 'system.tables.create_table_query' is not under aggregate function

-- ✓ split it into single-value queries, or wrap it in an aggregate function
SELECT countIf(countSubstrings(create_table_query,'TTL ')>0) AS n_with_ttl FROM system.tables WHERE …;
```

### General rules

1. The criterion logic must treat both "**the assertion is empty**" and "**the assertion errored**" as **failure**:
   `case "${B4:-}" in ''|*[!0-9]*) OK=no;; 0) OK=no;; esac`
2. Keep at least one assertion that proves "**the config was really loaded**" (this case `logger.level=warning`)
3. Rehearsal pass ≠ semantic correctness —— the former only proves "it didn't crash"; the latter requires **positively asserting that the target
   behaviour happened**

---

## Patch to apply (`scripts/config-preflight-2phase.sh`, run by the foreground session)

```bash
# ① Assertion ④ should take a machine-readable value (the old version only printed it; the criterion never checked it at all)
T4="('text_log','background_schedule_pool_log','query_metric_log','error_log')"
B4=$(P "SELECT countIf(countSubstrings(create_table_query,'TTL event_date')>0) FROM system.tables WHERE database='system' AND name IN $T4")
echo "   tables with a TTL = ${B4:-<empty>}"

# ② Add "empty/error = failure" to the criterion
case "${B4:-}" in
  ''|*[!0-9]*) OK=no; echo "✗ ④ the assertion has no data/errored (B4='${B4:-}') → false pass (was the subject table lazily created and never built?)";;
  0)           OK=no; echo "✗ ④ the new config did not take effect on any existing table";;
esac

# ③ Add the create-table hook to Phase A
AEXTRA="$HOME/.hermes/data/proposed/preflight-phaseA-extra.sql"
[ -f "$AEXTRA" ] && { echo "   applying the Phase-A creation script: $AEXTRA"; P "$(cat "$AEXTRA")"; }
```
