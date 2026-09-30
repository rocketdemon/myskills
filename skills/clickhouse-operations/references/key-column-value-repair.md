# Repairing a wrong value on a MergeTree "key column" + collapsing multiple versions of the same key

Scenario: an existing row holds a wrong value (here Langfuse `traces.timestamp` drifted by +8h) and needs to be
changed back to the correct value.
Triggers: fix data / correct a timestamp / repair historical rows / collapse duplicate versions / clear future dates.

> **Safety and the evidence chain during the bulk-execution phase** are covered separately in
> `bulk-mutation-execution-safety.md`: a generator whose `multiIf` omits the `id = ` prefix → `Code 43`, and **the
> multi-statement execution aborts right there, silently skipping the statements that follow** (measured: 13 rows
> deleted with no replacement); the pre-delete check that "the replacement row is already in place"; `system.query_log`
> / `system.mutations` forensics; the recipe for reloading an accidental delete; and the measured numbers from three
> rounds of truth-criterion convergence (loose criterion → same-family truth missed → independent authoritative source).

**Execution discipline (this one was paid for on 66 entities)**: pilot on **1 entity** before batching, confirm
convergence and semantic correctness, then go full scale. In this case the pilot immediately killed "`ALTER … UPDATE`
a key column" as a **fundamentally infeasible** option — cutting straight to all 66 would have meant running
destructive operations on SQL that has no feasible path.
Live schema: `default.traces` = `ReplicatedReplacingMergeTree(version=event_ts, is_deleted)`,
`PARTITION BY toYYYYMM(timestamp)`, `ORDER BY (project_id, toDate(timestamp), id)`.

## 1. First check whether the column can be UPDATEd at all — key columns cannot

```sql
ALTER TABLE default.traces UPDATE timestamp = toDateTime64('…',3,'UTC') WHERE id='…';
```
```
Code: 420. DB::Exception: Cannot UPDATE key column `timestamp`. (CANNOT_UPDATE_COLUMN)
```
Reason: the column is a member of `ORDER BY` / `PRIMARY KEY`. **Check the keys before writing a plan**:

```sql
SELECT sorting_key, primary_key, partition_key FROM system.tables
WHERE database='default' AND name='traces';
```

The failure leaves **zero trace** (to confirm nothing was touched, re-check the pre-change state: row count and
version count unchanged).
⇒ Conclusion: **a key-column value cannot be changed in place; the only route is "delete the old row + insert a
new row".**

## 2. The workable approach: INSERT the corrected row → mutation-DELETE the old row → OPTIMIZE FINAL

```sql
SET mutations_sync = 2;   -- make ALTER wait synchronously for completion

-- ① Insert the corrected row: template = this entity's newest version (max(event_ts)),
--    replace only the wrong column; event_ts = now64(3); is_deleted=0
INSERT INTO default.traces
SELECT id,
       toDateTime64('<true value>', 3, 'UTC') AS timestamp,   -- change only this column; list the rest in table-column order, unchanged
       name, user_id, metadata, release, version, project_id, environment, public,
       bookmarked, tags, input, output, session_id, created_at, updated_at,
       now64(3) AS event_ts, toUInt8(0) AS is_deleted
FROM default.traces
WHERE id = '<id>'
ORDER BY event_ts DESC
LIMIT 1;

-- ② Physically delete the old rows (the predicate uses `!= true value`, which naturally
--    excludes the row just inserted in ①)
ALTER TABLE default.traces
  DELETE WHERE id IN (<id list>)
    AND timestamp != multiIf(id='<a>', toDateTime64('<true value a>',3,'UTC'),
                              id='<b>', toDateTime64('<true value b>',3,'UTC'),
                              timestamp);

-- ③ Collapse multiple versions of the same key (count() is inflated before ReplacingMergeTree merges them)
OPTIMIZE TABLE default.traces FINAL;
```

**Why a mutation DELETE and not a lightweight `DELETE FROM`**: a lightweight delete is a **marker** (it writes a row
with `is_deleted=1` and version = the deletion instant) ⇒ it **races on version** against the new row from ①; if the
delete marker's version is larger, the whole entity "disappears" under Replacing semantics. A mutation DELETE is a
**physical removal** — no version race, and no zombie `is_deleted=1` row left behind.

**Order**: INSERT first, then DELETE. Reverse it and the template row is already gone, with nothing to copy.
**Batching**: N entities = N INSERTs (each copying its own newest version) + **1 batch mutation DELETE** (`multiIf`
mapping id→true value), which rewrites N-1 fewer parts than N separate DELETEs.

## 3. Verification (three checks, none of them optional)

1. **Row-by-row truth comparison (strongest)**: build a `UNION ALL` CTE of `(id, true value)` and `INNER JOIN` it
   against the source table: `countIf(t.timestamp != tr.true_ts)` should be **0**, and `max(abs(toUnixTimestamp64Milli
   difference))` should be **0 ms**.
2. **Row-count convergence**: `SELECT count() FROM (SELECT id FROM t GROUP BY id HAVING count()>1)` should be 0
   (one row per entity).
3. **Symptom cleared**: here `countIf(timestamp > now())` = 0.
   While you are there: `SELECT mutation_id, is_done FROM system.mutations WHERE table='…' AND is_done=0` should be empty.

## 4. Four verification pitfalls (all hit for real)

| Pitfall | Symptom | Handling |
|---|---|---|
| Comparing `count()` straight against the "entity count" | measured 86 rows vs 65 expected → misread as a failed repair | that is **multiple versions per key** (21 entities each keep their own already-correct old version at the true value); after `OPTIMIZE … FINAL`, 86 → 66. **Run FINAL before reading the number** |
| After the repair, still using `abs(timestamp − event_ts) > 1h` to detect drift | **every freshly repaired row gets flagged as drifted** (old event time + new write time, the difference is bound to be large) | after a repair, switch to the **semantic criterion** `timestamp > event_ts` (creation time later than its own write time = physically impossible); use `abs` only as a hint |
| After a `LEFT JOIN` aggregate subquery, using `countIf(x IS NULL)` to count "no true value" rows | always 0 → false pass (measured: 4 zero-observation entities disguised as "true value = 1970-01-01") | a non-Nullable aggregate returns the **type's default value**, not NULL, when a row is missing; use the right side's **row count** (`count()` / `obs_cnt > 0`) |
| Writing "259 rows before execution" into the plan as a constant | minutes apart, measured 262 vs 259 (background merges collapse same-key rows; the set is dynamic) | for execution and export, always work from the snapshot taken **the moment you act** |

## 5. Scoping rule: filter by **mechanism signature**, not by visible symptom

Here the scope was first defined as "future rows" (`timestamp > now()`) ⇒ covering only **66/473 entities = 14%**,
missing every row that **drifted but is still in the past**.

- The drift mechanism is "another +8h·k on every rewrite" (measured: 469/473 are exact integer multiples of 8h),
  and its **impossibility signature** is **`timestamp > event_ts`** — independent of "whether it lands in the
  future", and applicable to any table that has a write-time column.
- Sweep every table with that signature (here: `traces` 1,284 rows/473 entities, `observations` 133 rows/59
  entities, `scores`/`dataset_run_items`/`event_log` empty, `blob_storage_file_log` 0).
- Method: validate the criterion on the **known damaged set** until it hits 100%, then run it across the whole
  database; measure set stability twice (only use it if both readings agree).
- Visible symptoms (future dates) are a **clue**, never a scope.
- ⚠️ **2026-09-21 correction**: `timestamp > event_ts` is itself only a **proxy**, with known slow misses — when
  "write lag > drift amount" the row does not satisfy it; and using it against another table (`observations`) drags
  in a mass of sub-second clock offsets (measured: only ~14 of 133 rows are true drift). After a full repair **the
  criterion must become "compare `(stored value − true value)` against an independent authoritative source: is it an
  exact multiple of 8h (tolerance ±90 s)"**, taking the authoritative source (the client's original events) as
  ground truth; detailed numbers in `bulk-mutation-execution-safety.md` §7.

## 6. Where the true value comes from, and cross-validation

| Source | Notes |
|---|---|
| The original ingested events (here the min of `body.timestamp` in MinIO) | most authoritative = the client's first write instant; must be fetched per entity (`ls`/`docker cp` inside the container; the host cannot read docker volumes) |
| The earliest time in a derived table (here `min(observations.start_time)`) | one SQL statement covers everything, but it **must be sampled and aligned row-by-row against the authoritative source** (here 66/66 EXACT, delta 0.000s) before you rely on it |
| Self-evidence in a business field (here `session_id` shaped like `20260911_154122_…` = the local start instant) | a third independent signal, used to corroborate the direction of the true value (true value +8h ≈ the session_id instant) |

⚠️ Filter for true-value validity: `> 2020-01-01` (exclude the 1970 default), `≤ the entity's first write instant`
(allow a few seconds of clock jitter).
⚠️ Residual blind spot: if two sources **drift in lockstep by the same amount** (the difference cancels), this
criterion cannot detect it and cannot recover the true value ⇒ cross-validate a sample against the authoritative source.

## 7. Side-effect check: do derived data sets need rebuilding?

After repairing the base table, confirm whether any consumer needs recomputation:

```sql
SELECT name, engine FROM system.tables WHERE database='default';
```
- `View` (here `analytics_traces` / `analytics_observations`) = **aggregated at query time** ⇒ no rebuild needed ✅
- `MaterializedView` / AggregatingMergeTree on-disk tables ⇒ only those need separate evaluation (recompute or mark stale)
