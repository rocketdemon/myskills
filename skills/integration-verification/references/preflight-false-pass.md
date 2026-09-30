# False passes in an isolated dry-run / check script

**When to read**: right before you say "verification passed" after an "isolated environment dry-run", a "check script", or a "full health check".

---

## Symptom

The script prints every assertion and finally reports **✅ PASS** — but several of those assertions were actually **empty results**,
and the one you really cared about was never tested at all.

## Root cause (measured on ClickHouse)

**The object under test may simply not exist in a clean environment.** `system.opentelemetry_span_log` is created
**lazily** only when a span is produced; in a bare container it does not exist → the assertion SQL returns empty → the verdict logic checks only "was there an error" →
**the empty result is read as ✅**. The same run stacked a second blind spot on top: the assertion SQL itself was wrong
(`count()` mixed with an ordinary column → `Code: 215 NOT_AN_AGGREGATE`), so the whole assertion failed, and the verdict again did not look at it.

## Rules

1. **A dry-run must explicitly create the object under test in the isolated environment**; do not assume it will be created automatically —
   build it by hand with the production DDL (`SELECT create_table_query FROM system.tables WHERE …` /
   `SHOW CREATE TABLE`), then move to the next stage
2. **Treat "the assertion is empty" as failure**: `case "${B4:-}" in ''|*[!0-9]*) FAIL;; esac`
3. **An error counts as failure too**: wrong SQL, query exception, timeout — none may be folded into "no anomaly"
4. **There must be one assertion proving that the config/parameter was really loaded** (in this case `logger.level=warning`).
   Without it you cannot tell "the config never took effect" from "the config took effect but did not reach the target" — and the latter is exactly what happened here:
   the config was indeed loaded, but the ttl embedded in `<engine>` does not apply to an **existing** table
5. **Use machine-readable values plus an explicit comparison in assertions**, not just a single `echo` line for a human to look at:
   ```
   B4=$(P "SELECT countIf(countSubstrings(create_table_query,'TTL event_date')>0) FROM system.tables WHERE …")
   echo "tables with TTL = ${B4:-<empty>}"
   ```
6. **"No error seen" ≠ "fault ruled out"**: a `grep` with no match only says "that keyword was not found"

## Reverse self-check (ask yourself before reporting)

- Behind this ✅, **which assertion has non-empty data backing it**?
- Is the first record of the data source **earlier** than the start/write moment of the observed object? (insufficient coverage gets read as "too clean")
- Is there any assertion that **positively proves the target behavior happened**, rather than "no counter-evidence"?
- Is the conclusion **directly verified** or **inferred from indirect signals** (file size, timestamp)? The latter is only a clue

## Checklist

```
□ The object under test really exists in the isolated environment (not lazily created / not installed)
□ Every assertion carries a "must have data" constraint
□ Empty result = failure (not pass)
□ The assertion SQL was run on its own and returns the expected shape
□ One positive assertion that "the config/parameter was loaded"
□ One positive assertion that "the target behavior happened"
□ Timeout/error is listed separately as "undeterminable" and not folded into pass
```

## Related

- `clickhouse-operations/references/system-log-ttl-alter-vs-config.md` — the full record and patch for this case
- The journald coverage blind spot in host management (same family: insufficient data-source coverage read as "no anomaly")
