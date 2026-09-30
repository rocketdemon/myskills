# Two config-reload pitfalls: a single-file bind mount failing to take effect + derived objects of a "definition-is-state" component (field-tested notes)

Companion reading:
- `references/config-change-preflight.md` (pre-go-live rehearsal / four assertions / auto-rollback playbook, **generic**)
- `references/system-log-shadow-table-ttl-attribution.md` § mechanism verdict (source-level conclusion on CH system logs' "definition mismatch → rotation at startup")

This doc adds two things the former doesn't cover: **① the config was changed but the container can't read it (with no error)**; **② when the config redefines objects on every startup, what a change derives/overwrites**.

---

## 1. The single-file bind mount inode pitfall: the config "doesn't take effect" and raises no error

**Trigger condition**: the config is mounted into the container as a **single file** (`-v /host/x.xml:/etc/.../x.xml:ro`), and the editing method **replaced the file**
(create-new + rename — `patch` / `write_file` / an editor's "safe write" all do this) ⇒ **the host inode changed**.
The running container is still attached to the **old inode** ⇒ what it reads is still the old content.

**Why dangerous**: no error, the container is `healthy`, the host file looks changed — this is "silent non-effect".
The "edit → restart → check health" flow yields **a false success**.

**Measured (one-shot container + same-shaped bind mount, reproducible)**

| Step | Seen inside the container |
|---|---|
| initial (host inode 13366) | `V1-OLD-CONTENT` ✓ |
| host replaces the file (inode → 13290, container still running) | **still `V1-OLD-CONTENT`** (reads the old inode) |
| after `docker restart <container>` | **`V2-NEW-CONTENT`**, in-container inode = 13290 ✓ |

⇒ **`docker restart` re-resolves the bind mount by path** (same for `docker compose restart <svc>`),
so "single-file bind mount + replace-style edit" **takes effect with one restart, no `--force-recreate` needed**;
but you **must verify explicitly**, never assume.

**Two-step check after editing config (both are mandatory)**

```bash
# ① whether the host inode was swapped (swapped ≠ failure, it just means you must restart for it to take effect)
stat -c 'host inode=%i size=%s' /path/to/live.xml
# ② whether the new content is visible inside the container — compare content, not mtime (mtime is unreliable on a bind mount)
docker exec <container> md5sum /etc/.../x.xml ; md5sum /path/to/live.xml    # expected: the two are equal
# if ② differs: docker restart <container> first, then compare again; only if it still differs consider recreating the container
```

**Workarounds** (pick one, but always do the two-step check)
- **preserve the inode** while editing: `cp` over the same file, or write with `tee` (easiest when the content is already elsewhere);
- or accept replace-style editing + explicitly schedule one `docker restart`;
- or `docker compose up -d --force-recreate <svc>` (cost: the container id/hostname changes, which may affect data recorded by hostname).

---

## 2. "Definition-is-state" components: a change derives objects, gets overwritten, and rolls back asymmetrically

There is a class of components whose objects are **not created once and fixed**, but **redefined from config on every startup**; when they differ from the existing object they
**create a new object and rename/freeze the old one** (ClickHouse system logs are typical: see the shadow-table reference § mechanism verdict;
similar components rebuild indexes/views/watchdogs from config).

For such components, the plan must **spell out 4 things in advance**, otherwise your own monitoring will judge it as a failure:

1. **A change is overwritten by config, not layered on top**: any runtime `ALTER`/manual tweak is replaced by the config version on the next startup
   (a local instance: an ALTER adding TTL was later rotated away at startup, and the TTL was lost). ⇒ "fixing it at the root" is only possible through config.
2. **Derived objects are an expected product**: rotation leaves behind an old object **with data** (`<name>_N`). If the monitoring criterion is
   "there should be no old object with data", it **will necessarily fire** — state in the plan that this is expected and give the reclamation steps
   (back up first → then delete → independent re-check), rather than leaving ops to think the fix failed.
3. **Reconcile the steady-state volume against the monitoring threshold first**: steady state ≈ daily growth × window. Example: a table grows ~30k rows/day, monitoring threshold 300k rows
   ⇒ choosing a 14-day window (~420k) **creates a new alert instead**, while 7 days (~210k) stays within the threshold.
   **The window and the threshold are two numbers that must be fixed together**; don't just pick the window "consistent with other objects".
   Estimating daily growth: `SELECT <date column>, count() FROM <t> WHERE <date column> >= today()-7 GROUP BY <date column>`.
   ⚠️ If the table itself records query behavior, **your own diagnostics also raise the rate** (observation is intervention).
4. **Asymmetric rollback**: after the config takes effect, rolling the config back leaves the existing object (now matching the new config) inconsistent with the old config ⇒
   the next startup **derives once more**. ⇒ Write "the extra cost the rollback pays" into the plan, not "restores the original state with one command".

---

## 3. Hardening the rehearsal: the extra assertions each of these two pitfalls needs

Beyond the "four assertions" in `config-change-preflight.md`, add three (a review found that missing any one can produce a false pass):

- **⑧ Phase A must make the "subject under test" genuinely exist**: on a fresh data directory objects are **lazily created**, and **the lazy-creation list differs by object**.
  If the subject is not in the auto-created list (e.g. `opentelemetry_span_log` is created only when spans exist),
  Phase A must **explicitly create it per production's current definition** (or generate data to trigger it), otherwise Phase B won't walk the "load existing object" path.
- **⑨ Object identity unchanged**: the new config should only change the **target attribute**. Use `SHOW CREATE` to compare "rehearsal result vs production pre-change" field by field;
  only the one expected difference is allowed — this also catches "the section merge mode differs from expectation" (e.g. replacing a whole section rather than key-by-key deep merging,
  which may drop `database`/`table` keys, causing the object to be created elsewhere).
- **⑩ Stability re-run**: **restart once more with the same config**, and assert no new derived object appears (no `_1` again).
  This upgrades "it's fine once edited" to "editing won't act on every startup"; it can be deferred to **the next natural restart**, saving an artificial downtime.

## 4. Two more pre-go-live self-checks

```
□ Will this change overwrite modifications someone made earlier with runtime commands? (config is the sole authority at startup)
□ Will the rolling/rebuild derive new objects, hit a monitoring threshold, or turn a rollback into "paying the same cost again"?
```
