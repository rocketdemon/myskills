# Langfuse `traces` timestamp skew — verdict reached (2026-09-16)

**Status: root cause located, with a controllable reproduction** (this document replaces the 2026-09-14 "conclusion
undecided" version; both hypotheses from back then — "iterating day by day and writing past its bounds" and "a
replay/backfill path" — **have been disproved**; do not reuse them).

---

## Root cause (one sentence)

Langfuse's **update path for "the same trace id written again"**:

```
worker: IngestionService.processTraceEventList
        → getClickhouseRecord(old row)          # read-modify-write
        → mergeTraceRecords → mergeRecords(…, immutableEntityKeys[Traces])
        → clickHouseWriter.addToQueue(Traces)
```

`immutableEntityKeys[Traces] = ["id","project_id","timestamp","created_at","environment"]`
⇒ **`timestamp` is carried over from the old CH row and written back**. With `TZ=Asia/Shanghai` on this deployment,
that read→write-back round trip **nets one local offset (+8h) per round**; upstream Langfuse assumes the container
is UTC, which is why nobody upstream hits it (and why it is absent from the official docs). **The client (the Hermes
plugin) is not at fault.**

## The physical-evidence chain (four steps, all re-runnable)

1. **The original ingested events prove the client payload is correct** — MinIO `langfuse` bucket:
   `/data/langfuse/events/<project>/trace/<trace_id>/<uuid>.json/xl.meta`
   (MinIO on a single disk inlines small objects into `xl.meta`, with the JSON after a binary header; see "Reusable
   techniques" below.)
   Measured: the **47 `trace-create` events** for trace `ffabf111…` have `body.timestamp` running from
   `07:44:11.886Z` to `16:59:59.379Z`, **all genuine UTC instants** (only ~10s off the directory mtime).
2. **Controllable reproduction** (`~/.hermes/scripts/ch5/repro.py`, POSTing directly to `/api/public/ingestion`):
   | Write # | Sent by client (UTC) | Stored by CH (converted to UTC) | Offset |
   |---|---|---|---|
   | 1 (new id) | 07:55:38.273Z | 07:55:38.273Z | **0 ✅** |
   | 2 | 07:55:42Z | 15:55:38.273Z | **+8h** |
   | 3 | 07:55:46Z | 23:55:38.273Z | **+16h** |
   | 4 | 07:56:30Z | 09-17 07:55:38.273Z | **+24h** (`repro2.py` confirms linearity) |
   ⇒ **The first write is perfectly correct; only "updates" drift, at a constant step of 8h = the container's local offset.**
3. **Environment**: `langfuse/docker-compose.yml` explicitly sets `TZ: Asia/Shanghai` (both the web and clickhouse
   sections), and the worker also runs CST in practice; `SELECT timezone()` = `Asia/Shanghai`.
   CH renders DateTime64 using the **server timezone**, while the Langfuse-side assistant parses that bare string as
   **UTC** → +8h every round.
4. **Seen only in the two affected classes**: the trace ids of `Hermes turn` / `Dialectic Agent` are **generated
   deterministically and rewritten over and over** (the Hermes plugin's `create_trace_id(seed=f"{session_id}::{task_id}")`
   → the same session writes the same trace every round), repeatedly triggering the update path; those written only
   once (`Minimal Deriver` 3,122 rows, `Create Long Summary`, `Create Short Summary`) are **all clean** ✓. The
   `observations` table is clean too (observations take a different path).

## Why it "looks like one row per day, marching into the future"

`traces` = `ReplacingMergeTree(event_ts)`, `ORDER BY (project_id, toDate(timestamp), id)`,
`PARTITION BY toYYYYMM(timestamp)`:

- after each drift the `timestamp` is a **new sorting key** (toDate changed) → the old row is **not merged away**,
  and N "versions" coexist
- multiple versions on the same date **are** collapsed, so **only the ones that crossed a date boundary survive**
- ⇒ the "same instant, +1 day at a time" sequence is the product of **cumulative +8h×N crossing date boundaries and
  being collapsed by toDate** — it is **not** backfill/replay/day-by-day iteration

## Reusable techniques (the general playbook for this kind of client-vs-server attribution)

1. **Use epoch differences as the criterion, never rendered strings**:
   ```sql
   SELECT name, count() AS n,
          countIf(abs(toUnixTimestamp64Milli(timestamp)-toUnixTimestamp64Milli(event_ts)) > 3600000) AS skew_gt1h,
          countIf(timestamp > now()) AS future
   FROM default.traces GROUP BY name ORDER BY skew_gt1h DESC;
   ```
2. **Fetch the original ingested events** (the evidence that the server is innocent):
   ```bash
   docker exec langfuse-minio-1 sh -c 'ls /data/langfuse/events/<project>/trace/<trace_id>'
   mkdir -p /tmp/ch5/ev && docker cp langfuse-minio-1:/data/.../<trace_id> /tmp/ch5/ev/   # don't parse inside the container
   ```
   then locally in Python: skip the binary header, `JSONDecoder().raw_decode()` from the first `{`, and print `type`
   / `body.timestamp` / `body.startTime` (script: `~/.hermes/scripts/ch5/trace_events.py`).
3. **Controllable reproduction** (minimal injection to separate client from server):
   `POST {base}/api/public/ingestion`, Basic auth = `base64(PUBLIC_KEY:SECRET_KEY)`,
   body `{"batch":[{"id":"<uuid>","type":"trace-create","timestamp":"<ISO Z>","body":{"id":"<32hex trace id>","timestamp":"<same value>","name":…,"sessionId":…}}]}`;
   send the same trace id N times and watch whether the CH value drifts linearly (`repro.py`).
4. **While you are there, check the deployment timezone**: the `TZ` in `docker-compose.yml`, `date` in each
   container, `SELECT timezone()`.

## Pitfalls (hit first-hand here)

- ⚠️ **Comparing the `DateTime64` string rendered by CH in server timezone as if it were UTC** → I briefly concluded
  "the first write drifts by 8h too"; re-checking the epoch with `toUnixTimestamp64Milli()` corrected it.
  **Cross-timezone comparison always goes through epoch numbers.**
- MinIO / BusyBox-family containers **have no `grep` (and no `--include`)**; don't expect to filter inside the
  container — `docker cp` it out and parse there.
- Using `rm -rf` directly to clear a temp directory trips the approval gate and gets **BLOCKED** (and must not be
  retried) → **use a new directory name** and clean up manually afterwards.
- Once-only writes and repeated rewrites behave completely differently: **start by counting "versions" per trace id**
  (`GROUP BY id` and count rows); only a version count >1 can drift.

## Fix options (recorded 2026-09-16; whether to execute is the user's call)

- **A, root fix**: change the Langfuse stack's `TZ` to `UTC` (both places in compose + explicitly on the worker),
  restart `langfuse-web`/`langfuse-worker`; immediately re-verify with `repro.py` that "a repeated write no longer
  drifts". This is a config change + container restart → it goes through the intercept/report process.
- **B, reduce the trigger**: the Hermes plugin folds `turn_id` into the trace id seed (each round gets its own trace;
  session grouping still goes through session_id) → the same trace is no longer updated repeatedly.
  (⛔ The user decided **not to pursue this** — A already stops new drift, and this path needs a plugin change + a
  gateway restart.)
- **C, data repair**: change the future rows back to their real instants (the true value can be derived from the same
  trace's `observations.start_time` / `event_ts`). **Destructive — wait until A/B is settled.**

## Related

- `references/system-log-remediation-checklist.md`, layer 4 (the incidental-discovery layer)
- a host-side reference (`w32time-service-state-and-drift-measurement.md`) — a genuine **clock** problem has a
  completely different fingerprint (`w32tm` unsynchronised → constant small offset + journald rotate); do not
  conflate the two
