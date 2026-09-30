# Execution safety and evidence chain for bulk correction SQL (a full-repair run)

Scenario: feeding N pre-generated "correction SQL" statements into ClickHouse (in this case 468 INSERTs + 1 bulk mutation DELETE, fixing 1,280 rows of drift data). Triggers: bulk data correction / generate-SQL-then-batch-execute / rewrite historical rows / re-issue a delete mutation.

Companion: `key-column-value-repair.md` (key columns cannot be UPDATEd; the INSERT + DELETE + FINAL approach and its verification pitfalls).
This file covers only the **execution phase**: how not to corrupt data, and how to obtain physical evidence when something goes wrong.

---

## 1. The shape a generator most easily gets wrong: `multiIf` must be written with the `id = ` prefix

```sql
-- ❌ wrong: treats id as the condition
timestamp != multiIf('id1', toDateTime64('…',3,'UTC'), 'id2', …, timestamp)
-- ✅ right
timestamp != multiIf(id='id1', toDateTime64('…',3,'UTC'),
                     id='id2', toDateTime64('…',3,'UTC'), timestamp)
```

Error:

```
Code: 43. DB::Exception: Illegal type String of argument (condition) of function multiIf.
Must be UInt8: … isZeroOrNull((default.traces.id IN ('0000…', …
```

**What is truly fatal is the secondary effect**: a multi-statement client (`clickhouse-client -n`) aborts at **this** statement, and **none of the following statements execute, with no indication at all**. The consequence chain here (all backed by physical evidence):

```
Generated SQL order: 468 INSERTs → ALTER DELETE on traces (❌ code 43) → 14 observations INSERTs → ALTER DELETE on observations
                                          ↑ aborts here ⇒ the following 14 INSERTs never execute
A later re-issued "delete-only, no insert" DELETE (same file re-run) ⇒ 13 observation rows deleted with no replacement ⇒ data loss
```

⇒ Three hard rules:

1. **One uniform generator template**: `id = '<id>', <literal>` (paired) — never hand-assemble.
2. **Split deletes and inserts into two files, executed twice**: run `INSERT…sql` to completion and **verify the replacements are in place**, then run `DELETE…sql`. If mixed into one multiquery, any single failure silently eats everything after it.
3. **Do a pre-check before deleting** (see §4) — this is the only place that can stop you when "there is no replacement".

## 2. Don't let a pipe swallow the exit code, and don't misread CH's back-reference to the SQL as an echo

```bash
# ❌ Counter-example: rc is tail's rc; the error message is only its tail
cat repair.sql | docker exec -i <ch> clickhouse-client -n | tail -25
echo "exit=$?"          # always 0, even when CH reported an error

# ✅ Correct: rc comes from the last command in the pipe (clickhouse-client); if you need truncation, don't use a pipe
cat repair.sql | docker exec -i <ch> clickhouse-client -n
echo "CLIENT_EXIT=$?"   # or set -o pipefail
```

Pitfall: the tail of the `tail -25` output contained `… toDateTime64('…'), timestamp);)` — which looks like "the SQL was echoed", **but is actually CH's exception message back-referencing the whole query at its tail**. Treating it as normal output ⇒ misjudging the run as successful.

## 3. The only physical evidence for "did it actually run": `system.query_log` + `system.mutations`

Not from printf, not from the exit code, and certainly not from guessing:

```sql
-- was the statement executed / did it raise (ExceptionBeforeStart = failed at parse or argument-type stage, never ran at all)
SELECT event_time, type, query_kind, exception_code, substring(exception,1,180) AS err
FROM system.query_log
WHERE position(query, 'ORDER BY event_ts DESC LIMIT 1') > 0      -- filter by a unique fragment from the generated statement
  AND position(query, 'default.observations') > 0
ORDER BY event_time DESC LIMIT 10;

-- did the mutation actually enqueue/complete (no record = the ALTER was never submitted)
SELECT mutation_id, is_done, latest_fail_reason, parts_to_do, command
FROM system.mutations ORDER BY create_time DESC LIMIT 5;
```

**The fastest self-check signal is the direction of the row-count change**: after execution the row count **rose instead of falling** (in this case 6,088 → 6,424) while this DELETE is absent from `system.mutations` ⇒ "the INSERT took effect, the DELETE did not".
(Do not explain "the row count rose" as "background writes got faster" — first rule out that your own delete didn't run.)

### 3.1 ⚠️ But "the difference between two `count()` calls" is not a result — it may be just a transient reading taken mid-load (post-hoc review)

The direction judgement above was correct, **but both of those numbers were later proven not to be a "result"**: a re-query of `system.query_log` showed that this round of repair was
**473 single-row INSERTs** (`written_rows=1` each, `exception_code=0`) spanning **17:35:10 → 17:40:04 UTC** (4m54s),
while the AFTER count was read at **17:39:53** ⇒ at read time about 137 rows had not yet landed; after the DELETE took effect the final row count was **4,989**.
So 6,424 is **neither the before nor the after value — merely a transient value taken "half-way through the load"**. It was used as a qualitative clue at the time, **with no quantitative alignment done**;
only when the user pressed "what happened after the row count rose inversely?" was each row re-queried — a step that **should have been done at the time and was not**.

⇒ Three disciplines:

1. **To assert "how many rows were inserted", sum `written_rows` from `system.query_log`**, not the difference of two `count()` calls:

   ```sql
   SELECT count() AS n_finish, sum(written_rows) AS rows_written,
          min(event_time) AS t_min, max(event_time) AS t_max
   FROM system.query_log
   WHERE type='QueryFinish' AND event_time BETWEEN '<start>' AND '<end>'
     AND query LIKE 'INSERT INTO default.traces%' FORMAT Vertical;
   ```

   `n_finish` = number of statements, `rows_written` = actual row count, `t_min/t_max` = the load window
   ⇒ if any single `count()` reading falls **inside** the window, it is not a result.
2. **To assert "how many rows now", use a `system.parts` snapshot taken at a single instant, and take one before and one after the insert/delete**:

   ```sql
   SELECT sum(rows) AS rows_now FROM system.parts WHERE active AND table='traces';
   ```

   The row count is a quantity that **moves on its own**: concurrent writes add rows, and `ReplacingMergeTree`'s background `MergeParts` collapses same-key rows
   (during the review, `system.part_log` did show merge events of 16 / 136 / 21 / 70 rows in the same period).
   **If there is any concurrent activity between two separate `count()` calls, their difference is not evidence.**
3. **Mark numbers that don't reconcile as "unexplained", and do not invent an explanation**: here `+336` and `473` differ by **137 rows**, and the last INSERT's completion time
   (17:40:04) is **later than** the count time (17:39:53) — this ordering could not be reconciled, and no intermediate-state snapshot was kept at the time ⇒ it cannot be reconstructed.
   What was recorded was "unexplained + next time switch to the snapshot rule", not a plausible-sounding narrative.

### 3.2 Before restating historical numbers, reconcile the definition — the same batch of repairs has both 468 and 473

- **468** = the **entity count** under the "compare against the authoritative truth" criterion = the delete scope (`DELETE WHERE id IN (468 items)`, corresponding to 1,280 rows)
- **473** = the **target count** under the loose "`time column > event_ts`" criterion = the number of INSERTs (the 5 extra ones are same-family anomalies)

Both numbers are correct, but **a citing must carry the criterion name**. It was written at the time as "468 rows of corrected values inserted", mistaking the entity count for a row count.
⇒ Append a **post-hoc correction** section to the durable record (a `RESULT.md` or similar) rather than correcting only in chat — later readers read the file, not the chat.

## 4. The last gate before deleting: pre-check that the replacement is in place

```sql
WITH tgt AS (SELECT '<id>' AS id, toDateTime64('<corrected value>',3,'UTC') AS ts
             UNION ALL SELECT '<id2>', toDateTime64('<corrected value2>',3,'UTC') /* × N */)
SELECT countIf(has_row = 0) AS traces_missing_fix_row, count() AS traces_checked
FROM (SELECT tgt.id, countIf(t.timestamp = tgt.ts) > 0 AS has_row
      FROM tgt LEFT JOIN default.traces t ON t.id = tgt.id
      GROUP BY tgt.id);
```

Only when `missing == 0` is generation/execution of the delete allowed; otherwise print which are missing and exit immediately (here, only after 468/468 passed was the delete clean).
Building this step into the generator itself (refusing to generate delete SQL) is more reliable than fixing it up afterwards.

## 5. Acceptance must distinguish "fixed" from "row lost" — this is the verifier's own pitfall

```python
# ❌ Counter-example: counts "row absent" as fixed too (the old value of course cannot be found ⇒ fixed)
fixed = [i for i in old if cur.get(i) != old[i]]
# ✅ Correct: three-way classification, with "row lost" blocking the conclusion
missing = [i for i in old if i not in cur]          # must raise an error, must not count as complete
still   = [i for i in old if i in cur and cur[i] in old_versions[i]]
fixed   = [i for i in old if i in cur and cur[i] not in old_versions[i]]
```

Similarly, **"the old value disappeared" does not mean "it is fixed"**: only "new value = expected value" counts as fixed.
Only when both criteria pass (new value correct ∧ no rows lost) may you say it is complete.

## 6. Recipe for re-loading after an accidental delete (prerequisite: the original values were exported before execution)

1. Before execution, export the affected rows: `SELECT * FROM t WHERE id IN (…) FORMAT TabSeparatedWithNames` → gzip (here 10.3 MB / 1,587 rows)
2. Re-load = pick the missing id rows out of this TSV, replace the target column with the **intended value** (here `old value − 8h×m`),
   and load back with `INSERT INTO t (<column list>) FORMAT TabSeparatedWithNames` (the column list matches the TSV header).
3. Re-verify: per-id count + business criteria (here `start_time ≤ event_ts` and no future rows).
4. The backup may contain **two versions of the same key** (here 15 rows / 14 ids) — aggregate by id before re-loading; don't mistake the duplicate for a missing row.

## 7. How the drift criterion converged (three rounds of measurement) — don't use a criterion that merely "looks reasonable"

| Criterion | Hits | Failure points |
|---|---|---|
| `time column > event_ts` (loose) | traces 1,284 rows / 473 entities; observations 133 rows | ① in the other table's 133 rows, **the vast majority are sub-second clock skew** (measured 72 ms), with only ~14 rows of true drift ② misses rows where "drift amount < write lag" |
| compare against a **same-family table**'s truth as an integer multiple of 8h | traces 460 rows / 164 entities | misses **synchronized drift**: both tables drift together and the difference cancels (309 entities measured in this class) ⇒ the scope is even smaller than the loose criterion |
| an **independent, never-drifting authoritative source** (here MinIO client-side raw events) | **1,280 rows / 468 entities** | ✅ adopted |

Two takeaways:

- **A criterion needs an "impossibility signature"**, and it must be validated for 100% hits on a **known-damaged set** before scanning the whole table;
- **Two sources within the same entity family will drift together** ⇒ the second source for cross-validation must lie **outside the write path** (client side).

**The fix value is not the "truth", but `stored value − 8h×m`** (preserving that row's own baseline value):

- `m = round((stored value − truth)/8h)`, tolerance ±90 s (the client event time and the row baseline value may legitimately differ by a few seconds);
- rows with a residual of 111–152 s still belong to the 8h drift family (the residual is large after rounding `m`); don't drop them just because they exceed tolerance;
- if you set the row directly to the authoritative truth, you also overwrite those few seconds of legitimate difference ⇒ introducing a new inconsistency.

## 8. How to fetch the raw event source (MinIO instance)

- Layout: `/data/<bucket>/events/<project>/trace/<trace_id>/<uuid>.json/xl.meta`
- **A slim image has no `tar` and no `find`** (`sh` is present) ⇒ packing inside the container is a dead end;
- `docker cp <container>:<entire prefix> <local dir>` works in **a single call** (streamed on the daemon side, not depending on tools inside the container):
  measured 434 MB / 5,029 entity directories in **7m24s** (`du -sh` in the same directory can measure the size first);
- per-entity `docker cp` also works but is slow (473 ≈ 20 min, and I switched to copying the whole tree partway through — **measure the size before deciding**);
- truth definition = the earliest `body.timestamp` among all events of that entity (the client's first-write moment);
- ⚠️ only the `trace` prefix exists, **no observation prefix** ⇒ the other table's truth cannot be fetched, only solved by constraint:
  `truth = stored value − 8h·m`, with `m` taking "the smallest integer that makes the result fall within `[parent entity truth, min(own write time, created_at)]`".
