# Langfuse trace skew: A fix execution record + C scope-measurement method (2026-09-21)

Companions: `references/langfuse-trace-time-anomalies.md` (root-cause verdict). This document covers the **execution
side**: how to apply and verify A, and how to measure C's scope before acting without lying to yourself.
⚠️ Corrects one error in an existing document: in that chain the TZ line numbers are **compose line 18 = clickhouse,
line 97 = langfuse-web**; the earlier "web #18, clickhouse #97" was a mix-up.

## 1. A is executed: only the CH container's TZ was changed, and the mechanism was verified by reverse control

**Change**: `langfuse/docker-compose.yml:18` `TZ: Asia/Shanghai` → `TZ: UTC` (**only the clickhouse section**; the
web/worker TZ has no effect on this chain — changing it just aligns with upstream).

**Pre-flight before executing — upgrading "is the TZ env var the driver" from inference to fact** (three-way control
on the same image):
```bash
for v in "" "TZ=UTC" "TZ=Asia/Shanghai"; do
  docker run --rm $v clickhouse/clickhouse-server:26.7 clickhouse-local --query "SELECT '$v', timezone()"
done
# no TZ → UTC   ← this image **defaults to UTC**
# TZ=UTC → UTC; TZ=Asia/Shanghai → Asia/Shanghai
```
⇒ **The CH server timezone is determined by the `TZ` environment variable.** Two counter-checks: inside the
container `/etc/localtime` points to `Etc/UTC` while `timezone()` returns `Asia/Shanghai`; and `<timezone>` in
`config.xml` is **commented out** (line 948) ⇒ neither localtime nor config is the driver. The compose line is what
made it CST.
⇒ Corollary: **reverting that line or deleting it both fix it**; explicitly writing `TZ: UTC` is recommended (intent
is clear, and it is immune to changes in the image default).

**Restart method**: `docker compose up -d clickhouse` — **up/recreate is required**; `docker compose restart` does
**not** re-resolve env, so TZ will not change. Criterion: the output shows `Recreate → Recreated → Starting → Started`.
⚠️ Running `docker compose up -d …` in the foreground gets rejected by a tool-side heuristic that classifies it as a
"long-running service" → use `background=true`.

## 2. Acceptance (4 checks; anything less is not a closed loop; all passed in practice)

| # | Criterion | Measured |
|---|---|---|
| 1 | `SELECT timezone()`; container `date`; `docker inspect \| grep ^TZ` | **UTC / UTC +0000 / TZ=UTC** ✅ |
| 2 | **Behavior-level (strongest)**: rerun `scripts/ch5/E5_discriminate.py` | `new row − old stored = **+0.00 h**` (a constant +8h before the fix) ✅ |
| 3 | Data-level: stored value of the row E5 newly wrote = the old stored value | drift **zero increment** ✅ |
| 4 | Incidental: `_N` count does not grow, no new `_2` | still 10, no `_2` ⇒ with matching definitions, **a container-level restart does not trigger rotation** ✅ |

**Side-effect scope**: anywhere that reads time as a "rendered string" flips from CST to UTC (the Langfuse UI now
matches upstream; the local scripts are written on the principle "always compare across timezones using
`toUnixTimestamp64Milli` numeric values" and are unaffected).
**Rollback**: restore the original compose + `up -d clickhouse` again (the pre-change compose and its sha256 are
archived; the diff is exactly 1 line).

## 3. C, the data repair: three pitfalls in measuring the scope before acting (all hit)

1. **JOIN inflates counts**: after `FROM traces t JOIN observations o ON o.trace_id = t.id`, `count()` counts
   **post-join rows** (measured: absurd numbers like 6,888 / 16,330 / 11,040), not trace rows.
   Correct form: **`GROUP BY id` aggregate `traces` first, then LEFT JOIN the already-aggregated `observations`**.
2. **`event_ts` = write time, `timestamp` = event time**: judging skew with `abs(timestamp − event_ts) > 1h` is a
   **proxy** (a normal difference is seconds), so don't use it as an exact equation (which is why max_drift need not
   be an integer multiple of 8h); and bucketing by `event_ts` to look at "newly written" rows shows that **freshly
   written rows are not in any bucket** (a row rewritten under the same id reuses its old event_ts) ⇒ don't use it as
   the only criterion for "no new additions after the fix" — **behavior-level experiments are more reliable**.
3. **The affected set is dynamic**: two `count()` calls minutes apart → **262 vs 259** (background merges collapse
   same-key rows) ⇒ execution must be based on the export taken **the moment you act**, never on numbers from minutes earlier.

## 4. Sources of the true value (which one C uses)

| Source | Coverage | Precision | Notes |
|---|---|---|---|
| `min(observations.start_time)` | 100% (measured: all 148/148 future rows have one) | matches the original events character-for-character in samples; a few differ by ≤90s | fastest, one SQL statement |
| min of the original MinIO event `body.timestamp` | depends on event retention | most authoritative = the client's first write instant | must be fetched per trace; tool `scripts/ch5/c_collect_truth.py` |

⚠️ **The MinIO slim image has no `find`**: the three `0`s printed when `find` reports not found are **not** evidence
that the events do not exist (a tool failure is not a fact — the same rule applies here). Probe layer by layer with
`ls`, or `docker cp` to the host and parse there.
⚠️ The host **cannot read** docker volumes (`/var/lib/docker/volumes/...` permission denied) ⇒ you must go through
`ls` inside the container / `docker cp`.
⚠️ Writing `rm -rf` into a command to clear a temp directory trips the approval gate → **use a fresh directory name**,
don't clear it.

## 5. Boundaries

- **A only stops new additions; it does not settle the old debt**: the existing rows remain — but **the scope
  measured here at the time (1,500 skewed rows / 148 future rows / 66 affected traces / 259 rows) is only 14% of the
  real drift**, see the **scope correction** in §6.
- **B, reducing the trigger** (not executed): have the plugin fold `turn_id` into the trace id seed so the same trace
  is no longer updated repeatedly; used on its own it treats the symptom (it cannot help the other rewrite paths).
  ⛔ The user decided **not to pursue it for now** (removed from the todo list).
- Other mechanisms / the physical-evidence chain: `references/langfuse-trace-time-anomalies.md` and
  `references/frozen-ttl-subtable-cleanup.md` (the same "back up first → drill → execute → verify independently" procedure).

## 6. C execution result + **scope correction** (2026-09-21)

**Execution (pilot → full scale, user-approved scope = 66 rows)**: 259 rows → **66 rows** (1 row per trace);
row-by-row truth comparison `checked 66 / mismatched 0 / max_delta_ms 0`; future rows 142 → **0**; whole table
6,246 → 6,061. First methodological stumble: `ALTER … UPDATE timestamp` was rejected (**key column**,
`Code 420 CANNOT_UPDATE_COLUMN`) ⇒ switched to `INSERT corrected row → ALTER … DELETE → OPTIMIZE … FINAL`.
Method / pitfalls / criteria: `references/key-column-value-repair.md`.

### ⚠️ Scope correction: those 66 rows were only 14%

The scope was selected by "future rows", which missed every row that **drifted but is still in the past**. Re-swept
table by table using the mechanism signature `timestamp > event_ts` (creation time later than its own write time =
impossible):

| Table | Total rows | Drifted rows | Drifted entities |
|---|---|---|---|
| traces | 6,061 | **1,284** | **473** |
| observations | 30,412 | **133** | 59 |
| scores / dataset_run_items / event_log | 0 (empty) | 0 | — |
| blob_storage_file_log | 8,849 | 0 | — |

- Fixed so far **66/473 = 14%**; the drifted dates span **June–September**; 469/473 are exactly **integer multiples
  of 8h** (one row carries 29 version rows) ⇒ confirming the "every rewrite stacks another +8h" mechanism.
- The set was measured twice with the same value (1,284 / 473) before being used as a baseline — never reuse numbers
  from minutes earlier.
- True values: 469 of the 473 are available (`min(observations.start_time)`); **4 have zero observations** (fallbacks
  needed: the original MinIO events / reverse-solving from `session_id` / approximating with `event_ts`); 5 true
  values are 0.2–2.7s later than the first write (clock jitter — allow tolerance).
- Third independent corroboration: `session_id` (`20260911_154122_…` = the local start instant) — the true value +8h
  should land near it.
- No derived tables need rebuilding: `analytics_traces` / `analytics_observations` are **plain VIEWs** (aggregated at
  query time).
- **Takeaway**: a visible symptom (future dates) is only a clue; the scope must be taken from the **mechanism
  signature** and swept across every table.

## 7. Incremental final verification + two "output-side" blind spots (2026-09-23, closed loop)

**Verification script** `~/.hermes/scripts/ch5/ch5_increment_verify.sh`: the criteria are hard-coded inside the
script (the last section prints `VERDICT: PASS/FAIL/UNKNOWN`, and "UNKNOWN must not be read as PASS") — dual-track
criteria = ① counts of `timestamp/start_time > event_ts` inside the incremental window ② the authoritative criterion
(original MinIO client events, which never drift): the number of "drifted traces".
Measured (09-23 16:48, 47h after A): incremental traces **0/847 rows**, observations **0/5,224 rows**, drifted traces
**0**, whole-table future rows **0/0**, pending mutations 0 ⇒ together with the 4 acceptance checks from the A
execution, the loop is closed.

⚠️ **Two "output-side" blind spots** (both hide inside a report that looks clean — correct criteria ≠ correct report):

1. **A wrapper script's `grep`/`head` can eat the criteria**: the old version's 4th section was
   `detector 2>&1 | grep -E 'MinIO 真值|traces 行数|漂移|clean|drifted|no_truth' | head -8`
   — measured: it printed 7 lines and **ate 10 lines**, and the eaten ones were exactly `small_offset` (**clean
   bucket, 4,975 rows**), `weird 3`, 3 anomalous samples and the `wrote` line ⇒ the output becomes **isomorphic to
   "genuinely no signal"**, and the report side sees only a lone `clean 0` that is constant 0.
   Fix: **do not filter on the data side** (summarize in the report if you want a summary) + a machine-checkable
   verdict in the final section; the same goes for `head` truncation (the `| head -18` in `c2_final_check.sh` was
   removed too).
2. **Dead fields get read as facts**: `stats["clean"]` in `c2_detect_v2.py` was never incremented (output always
   `clean 0`), and the report read that as "0 clean rows" (the actually-clean bucket is `small_offset`). Fix: delete
   the dead field + **print all four buckets exhaustively and mutually exclusively** (`drifted / small_offset /
   weird / no_truth`, each with a note) + a self-check that "**the sum over buckets == the traces row count**" —
   this guards against silent row loss such as "a newly added bucket was never counted".

**Bidirectional unit test**: `~/.hermes/scripts/test-ch5-increment-verify.py` (**26/26**, including a MUT case that
uses the backed-up old detector + old grep to reproduce the old chain and asserts that `small_offset` / "anomalous
samples" **must disappear** ⇒ proving the test exercises exactly the criterion that was fixed; it also covers the two
paths that must yield `UNKNOWN`: "truth file missing" and "detector failed").
Script hooks `CH5_TRUTH / CH5_OUT / CH5_TOL / CH5_CH` (overriding them **leaves an explicit trace**; default
behavior is word-for-word unchanged).
