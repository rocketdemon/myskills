# Langfuse traces skew — fix A executed (2026-09-21; supplements langfuse-trace-time-anomalies.md)

> The root cause ("the same-id trace is read-modify-written + CH renders in CST + Langfuse parses as UTC ⇒ +8h per
> round") is in `references/langfuse-trace-time-anomalies.md`. This document records **execution and verification** only.

## The change: exactly one line

The **clickhouse service**'s `environment.TZ: Asia/Shanghai → UTC` in `langfuse/docker-compose.yml`, then
`docker compose up -d clickhouse` (it takes effect on Recreate).

- ⚠️ **Scope correction**: **only the CH container needs changing**; the `langfuse-web` / `langfuse-worker` TZ is
  **unrelated** to this chain (the assistant's `new Date(str+'Z')` is timezone-independent) — changing them merely
  aligns with upstream and is optional
- ⚠️ **Don't misremember the line numbers** (an older document mixed them up): on this host **#18 = clickhouse
  (change it)**, **#97 = langfuse-web (leave it)**; before editing, always `grep -n "TZ:" docker-compose.yml` and
  look at the output, don't copy from memory
- **Incidental finding**: the `clickhouse/clickhouse-server:26.7` image's **default timezone is already UTC** — this
  deployment's line is what changed it to CST; writing `TZ: UTC` explicitly is more robust than deleting the line
  (clear intent, immune to changes in the image default)

## Pre-flight: a three-way control on the same image proves which source is authoritative (eliminating the inference step)

Looking only at the current state you cannot tell whether the `TZ` environment variable or `/etc/localtime` drives CH
(on this host the two contradict each other: `/etc/localtime → Etc/UTC` while `timezone()` = Asia/Shanghai, and
`<timezone>` in `config.xml` is commented out).

```bash
for tz in "" "UTC" "Asia/Shanghai"; do
  docker run --rm ${tz:+-e TZ=$tz} clickhouse/clickhouse-server:26.7 \
    clickhouse-local --query "SELECT '${tz:-none}' AS case, timezone() AS tz"
done
# none → UTC ; UTC → UTC ; Asia/Shanghai → Asia/Shanghai   ⇒ the TZ environment variable is the driver
```
`clickhouse-local` is the single-file tool shipped inside the image: it returns in seconds, `--rm` leaves nothing to
clean up, and **you do not need to actually start a server container**.
**Generalisation**: for any change where "several candidate sources compete for the same config value", it is worth
running this three-way control on the same image to upgrade inference into measurement.

## Post-change verification (4 checks; anything less is not a closed loop)

1. `SELECT timezone()` = **UTC**; `date` inside the container = `UTC +0000`; `TZ=UTC` in `docker inspect`
2. **Behavior-level (strongest)**: rerun `~/.hermes/scripts/ch5/E5_discriminate.py` → **`new row − old stored = +0.00 h`**
   (a constant +8h before the fix) ⇒ the read-modify-write no longer stacks 8 hours
3. **Data-level**: the stored value of the row E5 newly wrote = the old stored value (zero drift increment)
4. Incidentally confirm the system log rotation was not broken: the `_N` count is unchanged and **no** new `_2`
   appeared (with matching definitions, **a container-level restart does not trigger rotation** — another positive
   sample of the mechanism established earlier)

- **Existing rows untouched**: A only stops new additions; at execution time the existing skew (**1,500 rows / 147
  future rows**) is still there — that is **C**'s job
- ⚠️ **Criterion pitfall**: `event_ts` is the **event time** (not the write time) ⇒ "bucket by `event_ts` and look
  for newly written rows" cannot see rows that were just written; to judge "no new additions", trust **E5's stored-value
  delta**, or find another write-time column
- **Rollback**: `cp` the backup back over compose + `docker compose up -d clickhouse` (run
  `docker compose config --quiet` first to check the syntax)

## What to watch before executing C (the data repair)

`traces` is `ReplacingMergeTree(event_ts)` ⇒ old rows do not physically disappear; you must first decide between an
`ALTER … UPDATE` mutation and "INSERT a new version + OPTIMIZE to merge", and **back up first**. The true value can
be derived from the same trace's `observations.start_time` / `event_ts`.
