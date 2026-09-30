# Preflight rehearsal and rollback before a config change goes live (preflight playbook)

A post-mortem of a real ClickHouse failure. **Applicable to any component where "you edit a config file and must restart the process/container for it to take effect"**
(ClickHouse / PostgreSQL / Redis / nginx / systemd unit …). Not a ClickHouse-specific method.

> Companion: how to detect **configuration that silently fails to take effect** is covered by a companion verification skill;
> this doc covers how not to be taken down by configuration **before it takes effect**.

## Incident timeline (why this rule exists)

| Time (CST) | Event |
|---|---|
| 18:27:31 | Phase 1 script starts (edits config.d to add TTL + lower log level) |
| 18:28:05 | Stop dependent services → apply new config → `docker restart` |
| **18:28:37** | First fatal error `Code 36`; thereafter the container restarts every ~6s |
| 18:28:37→18:31:45 | **32 crashes**, `err.log` grows ~300MB, host load 14 |
| 18:31:48 | Script **auto-rolls back** (restores config backup), component starts successfully |
| 18:32:06 | Dependent services come back → **total downtime ≈ 3.7 minutes**, zero business data lost |

**Done right**: back up the original config before changing + forensic-copy the deleted objects (files + sha256) + a script with built-in health check and auto-rollback.
**Done wrong**: treating "XML syntax valid + column names correct + sha256 diff clean" as passing verification. All three sit in layers ①/② of the criterion hierarchy and
**cannot prove the component's own semantic rules**. It is exactly such a rule that killed the process:

```
Code: 36. DB::Exception: If 'engine' is specified for system table, TTL parameters
should be specified directly inside 'engine' and 'ttl' setting doesn't make sense.
(BAD_ARGUMENTS)
```

## Criterion hierarchy: only layer ③ can serve as the basis for going live

| Layer | Means | Proves | Does not prove |
|---|------|-----------|-------------|
| ① Syntax | XML/YAML parser | the file is well-formed | semantic validity (this failure died after ①) |
| ② Static rules | checking docs / upstream and image defaults (e.g. `awk` for `<engine>`) | known rules don't conflict | unknown rules, interaction between rules |
| ③ **One-shot instance rehearsal** | same-version image + new config mounted + run assertions | **the config can be genuinely loaded and work inside the component** | ← only this layer can be the basis for going live |
| ④ Production + auto-rollback | go to production, roll back when the health check fails | a safety net | the downtime cost has already been paid |

## The skeleton for ③: a two-phase one-shot instance

```bash
V2=/path/to/new/config.xml
PRE=preflight
IMG=component/image:same-version      # must be the same version as production
D=$HOME/scratch/preflight-datadir     # temporary data directory (delete afterwards)
mkdir -p "$D"; chmod 777 "$D"         # the container's uid is usually ≠ the host user

# Phase A: don't mount the new config; first let the component create its objects (tables/indexes/metadata)
docker run --rm -d --name "$PRE" -m 1g -v "$D:/var/lib/clickhouse" "$IMG"
sleep 75
docker exec "$PRE" <client> -q "SELECT name FROM system.tables WHERE database='system'"
docker rm -f "$PRE"                    # data stays in $D

# Phase B: same data directory + new config mounted → reproduce the production path of "loading metadata of already-existing objects"
docker run --rm -d --name "$PRE" -m 1g \
  -v "$D:/var/lib/clickhouse" \
  -v "$V2:/etc/clickhouse-server/config.d/new.xml:ro" \
  "$IMG"
sleep 60
# → run the four assertions below
docker rm -f "$PRE"
```

**Why two phases are mandatory**: on a fresh data directory these objects are **lazily created** (measured: only auto-creates
text_log/trace_log/metric_log/part_log/asynchronous_metric_log), whereas the real error occurs in the
"load metadata of **already-existing** objects" phase. **Starting just one empty container = a false pass** — this is exactly how the first version of this rehearsal fooled itself.

## The four assertions (missing any one means a false pass)

```bash
# ① The component answers real requests ("container Up" doesn't count)
docker exec "$PRE" <client> -q "SELECT 'ALIVE'"

# ② The config is actually loaded: assert a runtime value that appears only with the new config
docker exec "$PRE" <client> -q "SELECT value FROM system.server_settings WHERE name='logger.level'"
#    ↑ must be 'warning'. Without this, a broken mount / wrong path would also "pass"

# ③ Zero fatal errors in the log: grep by error code, don't eyeball the tail
docker cp "$PRE:/var/log/.../err.log" "$OUT/pre-err.log"
C36=$(grep -ac 'TTL parameters should be specified' "$OUT/pre-err.log" || true); C36=${C36:-NA}
[ "$C36" = "0" ] || echo "FAIL: Code36=$C36"

# ④ Reproduce production's shape: assert the new config applies to "already-existing" objects
docker exec "$PRE" <client> -q \
 "SELECT name, countSubstrings(create_table_query,'TTL event_date')>0 AS has_ttl
  FROM system.tables WHERE database='system' AND name IN (...)"
#    ↑ expected 1: only then is it equivalent to production
```

## ⚠️ The `grep -c` exit-code pitfall (this caused a misjudged "rehearsal failed" and a wasted stop)

```bash
# Wrong: grep -c exits 1 when the match count is 0 → the || branch runs too
C=$(grep -c 'pattern' file || echo "NA")     # 0 matches → C="0\nNA", the string comparison necessarily fails
# Right: let grep's output through, swallow only the exit code
C=$(grep -ac 'pattern' file || true); C=${C:-NA}
```

Same family: anywhere "count = criterion", watch out that `grep -c` / `pgrep` / `diff` return non-zero on **no match**.
The symptom is "FAIL reported when nothing is wrong" — **suspect the criterion script first, the subject under test second**.

## The six assertions (expanded: beyond the original four, ⑤ shape unchanged + ⑥ stability must be added)

**Precondition (Phase A must do this first)**: on a fresh data directory the target object **may not get created at all** —
measured: `opentelemetry_span_log` **does not exist** in an empty container (lazy creation covers only
text_log/trace_log/metric_log/part_log/asynchronous_metric_log). If the target object does not exist, then when Phase B mounts the new config it
**will not exercise the production path of "loading already-existing object metadata" → another false pass**.
Forcing the target object into existence must use **the component's own normal path** (here: run a sampled query +
`SYSTEM FLUSH LOGS` so it creates the table via its natural path); **do not hand-write a DDL to fabricate one** —
a hand-written definition need not be byte-for-byte identical to what the component generates, and the rehearsal then loses its fidelity.

**Fidelity assertion (after Phase A, before Phase B)**: **byte-for-byte diff** the rehearsal instance's object DDL
**against the production existing object's DDL**; they must be **exactly identical** (here 22 lines identical). If not ⇒ the rehearsal environment differs from the production shape, and later assertions are meaningless.

```bash
# ⑤ Shape unchanged: after rebuilding the same object, the table name/columns/SETTINGS must not change, and the diff must be exactly "the expected line"
docker exec "$PRE" <client> -q "SHOW CREATE TABLE <obj>" --format TabSeparatedRaw > "$OUT/pre_after.sql"
diff "$OUT/before_prod.sql" "$OUT/pre_after.sql"      # expected: only the one line you intend to change is added/removed
#   ↑ this also proves "you changed the same object" — if the config section is replaced wholesale (rather than deep-merged key by key),
#     it shows up as database/table/flush etc. keys being lost, or even the object being created under a different name
```

```bash
# ⑥ Stability (Phase C): restart once more with the same config → assert no new side effects / new objects
docker stop "$PRE"; docker run --rm -d --name "$PRE" ...（same data directory + same new config）...
sleep 30; <trigger the effect path, see below>
#   ↑ expected: the object count is unchanged (here: no second shadow table appears). If it changes again ⇒ the change "acts once every time",
#     and you must understand why before going live; don't treat it as a one-off cost
```

## ⚠️ When it takes effect: the change may happen on "first use/flush", not at process start

Measured here: 30 seconds after the Phase B container started, the object **was still in its old shape, not rebuilt**; it was only rebuilt after forcing one `SYSTEM FLUSH LOGS`
(i.e. the component's own processing path). Likewise, the 6-minute gap in production where "the container starts at 11:35 but the object only changes at 11:41" is the same cause.
⇒ **The acceptance flow must explicitly include a "trigger the effect path" step**, otherwise "not yet triggered" is misjudged as "change failed",
leading to a wrong rollback. The trigger depends on the component (ClickHouse system log tables = `SYSTEM FLUSH LOGS`;
cache/queue types = fire one real request).

## ⚠️ Editing pitfall: whole-file replacement changes the inode, while a single-file bind mount still reads the old inode (a near-miss silent failure)

Tools that **replace the whole file** such as `patch` / `write_file` create a new inode (measured 20468 → 13306).
If the config is a **single-file bind mount** (`-v host.xml:/etc/…/x.xml:ro`), **the running container still reads the old inode**:
the host md5 has changed, **the in-container md5 has not** — if you then restart the process without checking, you get "the config changed but didn't take effect"
**or** "assumed it took effect once edited". Two measured conclusions:

1. **After editing you must verify the in-container md5/inode** (`docker exec <c> md5sum <in-container path>` + `stat -c %i`);
   looking only at the host file is self-deception
2. **`docker restart` re-resolves the bind-mount path** (a one-shot-container controlled experiment: after replacement without restart = old content,
   after restart = new content with the in-container inode already the new value) ⇒ in such cases **you do not need to recreate the container**, a restart suffices;
   with a `cp` overwrite (inode preserved) not even a restart is needed

## ⚠️ For such changes, "rollback ≠ restoring the original state with one command"

When a component's effect mechanism is "compare the definition generated from config vs. the existing object's definition, rebuild if they differ",
**rolling back the config makes the object mismatch again → the next startup rebuilds once more** (here, an extra shadow object).
So the plan must state: the rollback itself costs one more rebuild; irreversible actions (e.g. deleting the old object) are flagged and confirmed separately.

## Production-side script skeleton (built-in auto-rollback)

```bash
BK="$HOME/.hermes/data/backups/<topic>-$(date +%Y%m%d_%H%M%S)"; mkdir -p "$BK"
LOG="$BK/run.log"; exec > >(tee -a "$LOG") 2>&1     # write every step to disk so it can be checked afterwards

# 0) Forensics + backup (mandatory before an irreversible action)
cp -v "$LIVE" "$BK/live.orig"; sha256sum "$LIVE" "$NEW"
#    if the change involves deletion/cleanup: store the deleted objects' directories/files + sha256 into $BK first

# 1) Stop dependent services (so they don't flood errors while the component is unavailable)
docker stop <deps...>

# 2) Apply config + restart
cp -v "$NEW" "$LIVE"; diff "$BK/live.orig" "$LIVE"   # print added/removed line counts to confirm only the intended change was made
docker restart <component>

# 3) Health check (probe every 5s, ceiling ~200s)
for i in $(seq 1 40); do
  H=$(docker inspect -f '{{.State.Health.Status}}' <c> 2>/dev/null || echo unknown)
  echo "  [$i] $H"; [ "$H" = healthy ] && break; sleep 5
done

# 4) On failure, auto-rollback; don't leave the system in a bad state
if [ "$H" != healthy ]; then
  cp -v "$BK/live.orig" "$LIVE"; docker restart <component>
  docker logs --tail 40 <component>        # leave the failure scene
  docker start <deps...>                   # bring the dependent services back
  exit 1
fi

# 5) Verify the config actually took effect (cite the four assertions above)
# 6) ★Bring the dependent services back first★ before any long verification wait (e.g. sleep 120 re-sampling), otherwise you wait for nothing
# 7) Resource and business-side comparison: docker stats / load, and whether the business row count is still growing
```

## Failure mode: a crash loop (not "doesn't start, so it exits")

A container with `restart: unless-stopped` and a bad config will **restart every few seconds** and flood stack traces into the log file
(measured: 32 crashes in 3 minutes, +300MB of logs). So:
- give the rehearsal container a memory cap (`-m 1g`) so it doesn't drag down production
- the production script **must** have a health check + auto-rollback; don't rely on a human noticing in time

## Pre-go-live plan self-check checklist

```
□ List the full object inventory (all similar tables/files/scripts), marking each "change / don't change / reason" — single-point sampling always misses some
□ For each "change" object: check doc rules + upstream/image defaults (e.g. awk for <engine>), not just syntax
□ Rehearsal: two phases + four assertions; only go to production if it passes
□ Production script: forensic backup + health check + auto-rollback + tee log
□ Clear stop/start order for dependent services; the restore comes before any long wait
□ Write out the rollback path (one command restores the original state); irreversible actions flagged and confirmed separately
□ Measurement definition: when measuring rate across a restart, use only the time window "after the change is complete" (old pre-change data in the buffer lands after
  the restart, making count() jump, so don't count it as new volume)
```
