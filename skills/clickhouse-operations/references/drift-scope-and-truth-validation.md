# Before bulk-fixing time data: validating the truth and the scope (step 0)

Source: the **preparation phase** of a full-scale correction of Langfuse `traces` (+8h drift).
Sister doc: `key-column-value-repair.md` (how to change; this doc covers only **how to decide which rows to change and to what**).

## 0. Conclusion first

1. **A proxy criterion can only be a "candidate-set generator", never the scope.** On the same data, three criteria give numbers
   differing by 3× (473 / 164 / unknown), each with its own blind spots.
2. **The truth must come from a source "independent of the object being fixed, and never drifting"** — here = the client-side raw ingested events.
   An in-database derived column (even the earliest one) may **drift together** with the object being fixed.
3. The criterion's form uses **exact millisecond divisibility by the drift step**, not "the size of the difference":
   `(toUnixTimestamp64Milli(stored value) − toUnixTimestamp64Milli(truth)) > 0  ∧  … % 28800000 = 0`.

## 1. Measured comparison of the three criteria (same data, same day)

| Criterion | Hits | Distortion direction (measured) |
|---|---|---|
| impossibility signature `col > event_ts` | traces **1,284 rows / 473 entities**; observations **133 rows** | **bidirectional distortion**: ① **false positives** — sub-second clock skew mixed in (of the 133 obs rows only ~12 are true drift, the rest differ by ~**72 ms**); ② **false negatives** — when the drift of 8h < that row's write lag, Δ goes negative (measured minimum Δ **4.543 h** = 8h − 3.46 h lag) |
| compare against an **in-database derived truth** as an 8h multiple (`min(observations.start_time)`) | **164 entities / 460 rows**, and **fully contained in the previous one** | **synchronized-drift blind spot** — both tables drift together ⇒ the difference cancels ⇒ measured **309 entities** hit by the previous criterion are judged "clean" by this one |
| compare against **client-side raw events** as an 8h multiple | the authoritative scope | highest cost: the events must be fetched first (§3) |

⇒ Approach: use the authoritative source to define the scope; use proxy criteria to **generate candidates** (here 473 ∪ 164), then re-check the candidates against the authoritative source.

## 2. Why the judgement must be "an exact integer multiple", not "the size of the difference"

- The drift mechanism is "on rewrite, parse the rendered string in the wrong timezone" ⇒ each time **+ an integer number of timezone offsets**, **the millisecond digits unchanged**.
  Measured: truth `07:44:11.886`, in-database `15:44:11.886` — millisecond-for-millisecond identical, exactly +8h.
- So "exact divisibility by the step" is a **strong signature** that separates "drift" from "clock skew / write lag / backfill".
- ⚠️ But **don't round to whole hours**: `dateDiff('hour', a, b) % 8 = 0` lets in false positives like 7.9 h and 8.4 h.
  Always go through milliseconds (`% 28800000`) — this session hit the "hour scale vs millisecond scale" discrepancy twice.

## 3. Fetching the authoritative truth in practice (MinIO / S3 backend, self-hosted Langfuse v3)

- **Layout**: `/data/langfuse/events/<project>/trace/<trace_id>/<uuid>.json/xl.meta`
  (newer MinIO stores an object as a `<uuid>.json/` **directory** + `xl.meta`, not a `.json` file).
- **Event JSON shape**: `{id, timestamp, type: "trace-create", body: {…entity fields…}}`;
  truth = the **min of `body.timestamp` over all events** in that trace directory (the client's first-write moment).
- ⚠️ **Only one entity prefix, `trace`** (measured: under `<project>/` there is only `trace`) ⇒ **observations have no independent event source**,
  so their truth can only be back-solved (§4).
- **Volume**: the whole `events` is 1.8 GB (the bulk is `otel` at 1.4 GB); the `trace` prefix is only **434 MB**.
- ⚠️ **The MinIO slim image has no `tar` and no `find`** ⇒ packing/searching inside the container is unavailable:
  - ✅ per-directory `docker cp <container>:/data/…/<trace_id> <dest>` (goes through the docker daemon, no tools needed inside the container)
  - ✅ easier: **one `docker cp` copies the whole prefix tree** (one command copies 434 MB / 5,029 directories)
  - ❌ `docker exec … tar` → `tar: command not found`
- After fetching the directories, extract `body.timestamp` by "walk + take the first parseable JSON by regex"
  (`xl.meta` has a binary header up front, so `raw_decode` must be attempted from each `{` position).

## 4. Observation truth: constraint back-solving (when there is no independent event source)

`truth = stored value − step·m` (m an integer ≥ 1), feasible conditions:

```
truth ≤ min(this row's event_ts, created_at)   -- the write time is never earlier than the span start
truth ≥ parent entity truth                    -- an observation is never earlier than the trace it belongs to
```

Measured (133 candidate rows): **unique solution 12 / no solution 99 / multiple solutions 0**.

- **"No solution" is itself the criterion for "this is not drift"** (those 99 rows are sub-second offsets).
- Only when "multiple solutions" appear is an external source needed (raw events / OTel prefix / a self-attesting business field).
- Implementation shape: loop m from 1 to 60, collect all feasible solutions, output bucketed by solution count, and **list separately** the multiple-solution and no-solution samples for human review.

## 5. Full-table review: list the complete inventory + give an N/A reason per item

Scanning only "the tables I know about" = missing tables: the first version here scanned only **6/12** (missing `dataset_run_items_rmt`,
`project_environments`, `schema_migrations`). Approach: `SHOW TABLES` → judge **whether the criterion applies** per table,
and if not, **write out the reason**:

| Table class | Verdict | Reason wording |
|---|---|---|
| `View` (`analytics_traces` / `analytics_observations` / `analytics_scores`) | N/A | stores no data, aggregates in real time at query time ⇒ follows the base table automatically after a change, **no rebuild needed** |
| empty tables (`scores` / `dataset_run_items` / `dataset_run_items_rmt`) | 0 | criterion applies but the table is empty |
| no version column (`event_log` is `MergeTree`, no `event_ts`) | N/A | no "write time" to compare |
| no time column (`schema_migrations` 3 columns / `project_environments` 2 columns) | N/A | there is simply no time field |

Script it: auto-select columns by "table → engine → available time columns → key columns"; if the three can't be assembled, mark N/A, **never skip silently**.

## 6. Reporting discipline

- A number obtained from a proxy criterion may only be reported as a "**candidate / lower bound**", and its **two failure directions must be given at the same time**.
- Before reporting "N rows of drift", you must be able to answer: what the criterion is, where the truth comes from, and whether the truth source **may drift in sync with the object**.
- The truth source must be validated first: **two independent sources aligned row by row** (here 66/66 EXACT, delta 0.000 s) +
  a third self-attesting signal (the local startup time embedded in `session_id`) — if the alignment fails, it may not be used as the truth.
- Scope numbers **fluctuate** (background merge/write); re-take a snapshot before execution, and when writing the "candidate set" into the plan, label the point in time.

## 7. Script hygiene (hit twice this session, in opposite directions)

When building SQL with Python `%` formatting, SQL's modulus operator must be written as `%%`; SQL that is **not** %-formatted uses a single `%`.
Mixing the two cases is the most error-prone, and the two errors look like this:

- `TypeError: not enough arguments for format string` (wrote `%` where `%%` was needed)
- ClickHouse-side `Syntax error` (wrote `%%` where it shouldn't be)
