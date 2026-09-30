# Fixing the CH health-check criterion: a 0-row active table causes a "TTL window not advancing" false alarm (2026-09-22)

Object: `~/.hermes/scripts/check-ch-system-tables.sh` (both a standalone health check and, called by `infra-check-boot.sh` section 8).

## Symptom

When the active table `system.opentelemetry_span_log` **has 0 rows**, `SELECT min(finish_date)` returns `1970-01-01`
⇒ always `< STALE_LIMIT` ⇒ it reports "TTL window not advancing" as a **false alarm** (measured once this time; it disappeared automatically as data
flowed in).

## Fix

Precondition the TTL-window criterion on "rows > 0":

```bash
if [ "${ROWS:-0}" -gt 0 ] 2>/dev/null && [ -n "${OLD:-}" ] && [ -n "${NEW:-}" ] && [[ "$OLD" < "$STALE_LIMIT" ]]; then
```

Meaning: only judge "is the window advancing" when there **is really data**; 0 rows is "nothing to judge", not "the TTL isn't deleting".

## New test hooks (default behaviour byte-for-byte unchanged; overriding leaves a trace)

| Hook | Effect |
|---|---|
| `CH_FAKE_Q` | Points at an executable that receives the SQL as `$1` and prints a stub result —— replaces `docker exec … clickhouse-client`; when overriding, stderr prints `WARN CH_FAKE_Q overriding the query executor: …` |
| `CH_STATUS` | Overrides the status-file path (default `~/.hermes/data/ch_system_tables_status.json` —— that file is used by the boot check as the criterion for "did this run really finish"; unit tests must not pollute it) |
| `CH` | Existing: the container name (default `langfuse-clickhouse-1`) |

The stub script must dispatch on SQL keywords (`count() FROM system.tables` / `min(finish_date)` / `name FROM system.tables` /
`extract(create_table_query` …), otherwise it introduces noise alarms such as "system-table inventory query is empty (anomaly)".

## Unit tests

`~/.hermes/scripts/test-check-ch-system-tables.py` (**4/4 PASS**, including mutation testing):

| Sample | Expected |
|---|---|
| A `ROWS=0, min=1970-01-01` | must **not** report "TTL window not advancing" (★ the fix point) |
| B `ROWS=100, min=2020-01-01` | must report (the window really is not advancing) |
| C `ROWS=100, min=today-2 days` | must not report |
| MUT removing the `[ "${ROWS:-0}" -gt 0 ]` precondition | A must turn red ⇒ proves the case tests that criterion |

A real production run (default mode) has no regression: the output is still "found 1 frozen `_N` subtable with rows
`['system.opentelemetry_span_log_0']`", consistent with the boot report; no "TTL window not advancing" false report.

## General takeaway (same family)

- How a criterion's return value behaves **when its input is the empty set** must be handled explicitly: ClickHouse's `min()` returns `1970-01-01`
  on an empty set, which is numerically indistinguishable from "the data is very old" ⇒ the criterion must first ask "is there any data", then ask
  "is the data fresh".
- Whenever you add a "test hook": **keep the default path byte-for-byte unchanged + leave a trace when overriding**, otherwise "filtered
  successfully" and "missed a report" look identical in the output.
