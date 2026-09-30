---
name: component-upgrade
description: Use when upgrading the version of a self-hosted component — a Docker container, database (ClickHouse/PostgreSQL/Redis), or a git-deployed service. Assess breaking changes, pick the exact target, plan the maintenance window, execute with verified backups, and roll back with evidence.
trigger: upgrading a container/database/middleware version; evaluating an upgrade path; writing an upgrade plan; "should I upgrade X to Y"; a service that self-updates its own platform
---

# Component Upgrade Playbook

A field-tested path from *"should we upgrade?"* to *"verified, or rolled back"*:

**assess → choose the exact target → gather facts → plan the window → backup (restore-verified) → execute → verify → roll back if needed.**

Two families are covered, and they fail in different ways:

| Family | How it is deployed | Backup unit | Characteristic trap |
|---|---|---|---|
| **Container / database** | `docker compose`, pinned image tags | volume snapshot or dump | mirror reachability, UID change, parts/metadata format |
| **Git-deployed service** | `git clone` + venv + systemd | DB dump + code snapshot | the upgrade tool installs a *moving branch*, not the tag you assessed |

---

> **Identifiers used in this skill.** The prose, checklists and references are written generically:
> `the app` / `the platform` is the component being upgraded, `~/.myapp/` is its home directory,
> `APP_HOME` is the environment variable that points at it, and `app_cli` is its own CLI package.
> The **scripts in `scripts/`** are concrete reference implementations written against one real platform
> (the open-source Hermes Agent: `~/.hermes/`, `HERMES_HOME`, `hermes_cli`, the `hermes-gateway` unit).
> Map those few names to your own platform before running them — the logic, the criteria and the
> evidence each script prints are unchanged. Do not treat an unresolvable path in a reference as a
> literal instruction: it is a placeholder for "wherever your own tooling lives".

---

## 0. The rules that decide success or failure

1. **Know whether your target is pinned.** If the upgrade tool installs a branch tip, your assessment has an expiry time — execute soon, and recompute drift right before executing.
2. **Prove reachability before the window.** Run `docker pull` / `git fetch` *independently* and early. Do not discover a blocked registry or a stale lock at 2 a.m.
3. **Backups are only valid if a restore was verified.** A tool's exit code is not evidence. Use a checksum manifest and a **negative test** (delete a file → the checker must FAIL).
4. **Verify the thing you came for** — not "the process is up". Health checks go green while the original bug is still unfixed.
5. **Never write `:latest`.** Pin the exact version/tag/commit; the upgrade then becomes explicit instead of accidental.
6. **Read the *apply* path of the tool, not its `--help`.** The check path and the apply path frequently have unequal guards.
7. **Config that is written is not config that is in effect.** Establish *who reads the value, and when*: watcher, per-request read, or construction-time snapshot.
8. **A migration rewrites files you did not list.** Enumerate by *executing* the migration engine; never by grepping its registry.
9. **Verify your instrument before you trust a FAIL.** A checker that reads a non-existent attribute, matches itself, or skips hidden directories produces false alarms with the same shape as real failures.
10. **Every claim in the plan needs a citation** — a changelog line, an issue number, a measured value. Jumping to conclusions is how upgrades break production.

---

## 1. Assessment checklist (complete all of it *before* writing a plan)

```
□ Current version vs target version — how far, and across how many major versions?
□ **Target selector of the upgrade tool**: does it install a branch tip or a version (tag / image tag)?
  Can it be pinned to an exact version?  If it cannot: the target moves ⇒ assess, then execute soon,
  and recompute drift against the tip of that moment before executing.
□ **Is the fetch/pull step itself going to succeed?** Run `git fetch` (or `docker pull`) on its own first.
  (A stale `.git/*.lock` blocks fetch *reliably* while read-only commands such as `git ls-remote`
  still succeed — this is easily misdiagnosed as network flakiness.)
□ Breaking changes reviewed version by version (changelog + release notes + issue tracker)
□ Data volume measured (<100 MB ⇒ tar is fine; >100 MB ⇒ volume snapshot or DB dump)
□ Image tag pinned (no `:latest`; pin e.g. `:26.7.1`)
□ Registry/network reachability verified by an actual pull (in mainland China: check the mirror config)
□ UID/User compatibility (`docker inspect --format '{{.Config.User}}'`, old vs new)
□ Dependent services: who depends on this, and which versions do they support?
□ Custom configuration: do the bind-mounted config files remain valid in the new version?
□ Backup path decided (tar / volume snapshot / database dump) + **restore rehearsed**
□ Rollback path decided (restore data + revert image tag/tag + restart)
□ Maintenance window estimated (do dependents need to stop first?)
□ Verification list prepared (version, row counts, background tasks, logs, dependent API)
```

---

## 2. Choose the exact target — do not "just upgrade to latest"

### 2.1 A moving target invalidates the assessment

Determine what the tool actually installs **by reading its source**, not its help text. A real case: the whole
assessment was done against release tags, while the command performed `git checkout main` and fast-forwarded to
the tip of `origin/main` — a different artifact, on a target that moved twice within twenty minutes.
If there is no pin option, say so in the plan: mark the conclusion as **approximate target**, and recompute drift
(frontier migration step, whether untracked files are tracked at the target commit, out-of-lockfile packages,
private APIs used by your own scripts) immediately before executing.

### 2.2 Cross three constraints per candidate version, take the smallest winner

Large jumps should not default to "newest". For each candidate tag, list the constraints and pick the
smallest tag that satisfies all of them:

```bash
for t in $(git tag --sort=creatordate | tail -12); do
  printf "%-10s " "$t"
  printf "py=%-8s " "$(git show $t:pyproject.toml 2>/dev/null | grep -oE 'requires-python = "[^"]*"')"
  printf "migrations=%s\n" "$(git diff --name-only OLD..$t -- migrations/ | wc -l)"
done
```

Three constraints that commonly bind:

| Constraint | How to read it |
|---|---|
| Runtime requirement (`requires-python`, Node major, glibc) | jumpy — it can change between patch releases |
| Client/dependent SDK floor | official compatibility table (often `docs/changelog/compatibility-guide.*`) |
| DB schema change | migrations tree hash (see 2.4) |

### 2.3 Which release contains the fix you need

```bash
git tag --contains FIX_COMMIT | sort -V | head -1          # earliest tag containing the fix
git merge-base --is-ancestor FIX_COMMIT TAG && echo yes || echo no
```

### 2.4 Is the schema changing? Prove it with a tree hash, not a file list

```bash
git ls-tree -r OLD_REV -- migrations/ | md5sum
git ls-tree -r NEW_REV -- migrations/ | md5sum    # identical hash ⇒ zero schema change
```

### 2.5 The "middle major" trap

Read release notes looking for **the release that admits it introduced a problem** ("shipped a large rewrite of
…, and for some installs it made X fragile"). Stopping on that major means volunteering to hit it.
Prefer the newest patch, not the first tag that crosses a major boundary.

### 2.6 Rehearse before you commit

```bash
git worktree add --detach /tmp/probe TARGET_TAG
cd /tmp/probe && uv sync --python "$(python3 -V | cut -d' ' -f2)"   # a real install; exit 0 is the evidence
git worktree remove /tmp/probe --force
```

> A dependency resolver reporting "Resolved N packages in 3ms" is a **cache hit, not a feasibility proof**.
> Only a real install in an isolated worktree counts.

---

## 3. Gather facts (facts first, opinions later)

### Container / database

```bash
# current version and declared image
docker exec CONTAINER COMPONENT --version
docker inspect CONTAINER --format '{{.Config.Image}}'

# data volume (separate business data from system/log tables)
docker exec CONTAINER DB_CLI -q "SELECT formatReadableSize(sum(total_bytes)) FROM system.tables WHERE database NOT IN ('system')"

# table shapes and engines
docker exec CONTAINER DB_CLI -q "SELECT name, engine, total_rows FROM system.tables WHERE database NOT IN ('system') ORDER BY total_bytes DESC"

# parts fragmentation (MergeTree-family engines)
docker exec CONTAINER DB_CLI -q "SELECT table, count() parts, sum(rows) rows FROM system.parts WHERE active GROUP BY table"

# bind mounts (which config files are actually mounted)
docker inspect CONTAINER --format '{{range .Mounts}}{{if eq .Type "bind"}}{{.Source}} -> {{.Destination}}{{"\n"}}{{end}}{{end}}'

# dependents and their healthchecks
grep -A10 'COMPONENT:' docker-compose.yml
```

### Git-deployed service

- **Which interpreter does it actually run?** Read `ExecStart` in the systemd unit; do not assume `.venv`.
  (A real repository had both `.venv/` and `NAME-venv/`; only the first was referenced by systemd.)
- **Authoritative config source is the upstream `.env.template`** — never inferred from class field names
  (nested prefixes like `A__B` vs `A_B` may both "work" while only one is canonical).
- **Untracked/uncommitted changes**: classify them before deciding their fate (section 9.2).
- **Your own scripts' private imports**: `grep -rnE '^\s*(from|import)\s+APP_MODULE' your-scripts/` — an upgrade
  that refactors those modules silently breaks tooling.

---

## 4. Breaking-change research

- Read the **Backward Incompatible Changes** section of *every* intermediate release, not only the target's.
- Search the issue tracker for the target version plus the failure keywords of your engine
  (e.g. `CORRUPTED_DATA`, "failed to start", "metadata").
- Read the release blog posts between current and target — they usually quantify performance work,
  which is what justifies the upgrade in the first place.
- For each breaking change, state explicitly whether it touches *your* deployment
  (credentials, query syntax, engine behavior, config keys).

Changelog anchors worth knowing:

| Product | Where |
|---|---|
| ClickHouse | `clickhouse.com/docs/resources/changelogs/oss/YEAR`; `github.com/ClickHouse/ClickHouse/releases`; issues with `CORRUPTED_DATA` |
| PostgreSQL | `postgresql.org/docs/release/`; major-version upgrade guide |
| Redis | `github.com/redis/redis/releases`; breaking changes per major |
| Generic | upstream `CHANGELOG.md` + `docs/` upgrade notes + the issue tracker, filtered by version |

---

## 5. Client and dependent compatibility — the most-missed step

Order of operations: **upgrade the data layer first, then the application** — and only after the data layer's
verification passes.

- Read the official compatibility table; check the version the **dependent** actually has installed.
- Server upgrades routinely raise the *client SDK* floor. If the client is pinned inside another project's
  dependency file, the upgrade cost spills into that project **and gets reverted by its own updates** —
  in that case prefer a **smaller target that needs no SDK change**.
- A green health check only proves the server is alive. It does **not** prove the host client can call it.
  Run the host's real call surface: enumerate the methods the host code actually uses
  (`grep -rhoE '\.(method_a|method_b)\(' HOST_DIR`), call each one from the host's own venv,
  and record pass/fail per method. `10/10 passed` is the strongest available evidence that the upgrade
  will not break its consumer.
- If the platform ships sidecar processes (plugins, MCP servers, workers) in a **shared venv**, a major SDK bump
  can break them at import time. Decide "will this sidecar be collaterally broken?" in three steps:
  ① read the `command` in config; ② resolve that command under the systemd unit's `PATH` (the host venv is often
  first, so a bare `python3` *is* the host venv's python); ③ confirm against the running process line (`ps -ef`).
  The root fix is a **dedicated venv with an upper version bound** for each sidecar, decoupled from the host.

---

## 6. Risk matrix

| Dimension | Low | Medium | High |
|---|---|---|---|
| Version span | within the same major | one major | several majors |
| Topology | single node | 2–3 nodes | multi-node cluster |
| Data volume | <100 MB | 100 MB – 10 GB | >10 GB |
| Production dependency | off the critical path | interruption acceptable | must not be interrupted |
| Custom configuration | almost none | a few keys | heavily customized |
| Engine maturity | stable engine | — | experimental engine |
| Registry reachability | cache/mirror confirmed by a pull | mirror configured but untested | direct pull required |

**Known high-risk combinations**

- Cross-major upgrades can change on-disk metadata formats; an unclean shutdown before the upgrade makes this worse.
- Coordinator/keeper log-format changes can prevent even a single node from starting.
- Within the same major (e.g. `26.5 → 26.7`) risk is usually very low — say so explicitly rather than
  presenting a uniform risk level.

---

## 7. Maintenance-window estimate

| Step | Typical |
|---|---|
| Backup | data-volume dependent (tens of MB ⇒ seconds) |
| Stop dependents | ~10 s |
| Stop the component | ~5 s |
| Pull artifact | ~60 s (network dependent) |
| Start new version (with health check) | ~30 s |
| Start dependents | ~10 s |
| Verify | ~30 s |

Publish the estimate as a window, and state what happens if the window is exceeded (that is what the rollback
plan is for).

---

## 8. Backup that has actually been verified

Three rules — see `references/backup-and-restore-verification.md` for the full recipe and the counterexamples:

1. **The manifest comes from a source of truth, not from the backup tool's narration.**
   Build it from `dist-info/RECORD`, a `pg_dump` listing, or `find -printf '%s %p'` — not from "the copy said OK".
2. **The verdict must not be the copy command's exit code.** Copying and judging are separate steps; the verdict
   comes from a per-file checksum comparison against the manifest.
3. **Test the checker in both directions.** On an empty directory it must PASS; delete one file or flip one byte
   and it must FAIL. *A validator that can only say PASS is not a validator.*

What must be in the backup, beyond the obvious data volume:

| Item | Why |
|---|---|
| Config file(s) **and** the app's `.env` | the migration rewrites some of them (section 10) |
| Out-of-lockfile packages | a dependency sync deletes them; the feature goes silently dead |
| Untracked working-tree files | an upgrade's stash/reset cycle can leave them off-disk |
| The manifest + the restore script | a backup you cannot restore is not a backup |

Restore-script traps (all observed): `cp -a src dest/` **nests** when `dest/src` already exists
(use `cp -a "$SRC/." "$DEST/"`); read-only files (mode 444) in the backup make a second run fail with
`Permission denied` even though the data is fine (`cp -a --remove-destination`); and a script that only knows
how to print `FAILED` will eventually lie to you.

---

## 9. Execute

### 9.1 Container / database template

```bash
# 1. PULL (verify reachability first)
docker pull IMAGE:EXACT_VERSION

# 2. BACKUP  (verify with a checksum manifest — section 8)
tar -czf /tmp/NAME_backup_$(date +%Y%m%d).tar.gz -C VOLUME_PATH .

# 3. STOP dependents, then the component
docker compose stop DEPENDENT_SERVICES
docker compose stop COMPONENT

# 4. PIN + start
#    edit docker-compose.yml: image: IMAGE:EXACT_VERSION
docker compose up -d COMPONENT
#    wait for the healthcheck (docker compose ps)

# 5. VERIFY (section 11), then start dependents
docker compose up -d DEPENDENT_SERVICES
```

In mainland China, verify registry reachability by an actual pull, check `registry-mirrors` in
`/etc/docker/daemon.json`, and remember that `docker manifest inspect` times out far more often than `docker pull`.
**A configured mirror is not a working mirror.**

### 9.2 Git-deployed service template

```bash
# 0. Classify local changes BEFORE touching anything
git diff --numstat                      # whole-file add = delete ⇒ line-ending rewrite signature
git diff --ignore-cr-at-eol --numstat   # what remains is the real local patch
#    back up the real patch separately: checkout/reset will erase it

# 1. Snapshot code + data
git rev-parse HEAD > /tmp/pre_upgrade_commit
pg_dump ... > /tmp/pre_upgrade.sql

# 2. Isolated rehearsal (section 2.6), then the maintenance window

# 3. Switch code, rewrite config, restart — as ONE continuous action (see below)

# 4. Re-apply local patches by anchor, not by line offset
grep -n 'KNOWN_ADJACENT_LINE\|FIELD_NAME' FILE        # locate the anchor; confirm the field is missing
#    insert with an edit tool, then verify BOTH ways:
git diff --numstat                      # expect: 1  0  FILE
git diff --ignore-cr-at-eol --numstat   # identical ⇒ exactly one line added, line endings intact
```

> The criterion is that the **two** `--numstat` outputs agree. If only the CR-ignoring one shows 1 line, the edit
> rewrote line endings across the whole file — in a CRLF repository that manufactures hundreds of phantom changes
> and poisons every later `git status` judgement.

**The half-upgraded window.** Never leave "disk is new, process is old" standing. If the new version turns a loud
error into a silent degradation and the enabling flag was not set, the failure mode changes from obvious to
invisible. Bind the code switch and the config change into one action, close the window as fast as possible, and
tell the user the window exists.

**When the upgrade command is the platform's own self-update**, read
`references/platform-self-upgrade-mechanics.md` first. Three behaviours bite every time:
it kills the whole process tree that invoked it (so a user must run it from an external shell);
it restarts services by itself (do not wait inside the same turn); and it runs config migration itself.
A non-zero exit of `2` usually means a concurrent-updater lock, not corruption — wait, do not retry.

---

## 10. Config migration: enumerate by execution, never by registry

**The criterion is what the migration engine actually wrote, not what the registry lists.**
In one measured host upgrade, reading the registry produced "4 keys change"; *actually running* the target version's
engine produced **6** — the two missing ones came from two inline factory entries registered under the
same version number. Grepping only for named functions misses factory-style steps entirely.

```bash
git -C REPO worktree add --detach /tmp/probe TARGET_TAG      # does not disturb the live checkout
mkdir -p /tmp/probe-home && cp ~/.myapp/config.yaml /tmp/probe-home/config.yaml   # a COPY: migration writes
```

```python
# Bind the real signature before calling — never guess arity; signatures change between versions
import inspect
from app.config import check_config_version
from app import config_migrations as cm

cur, latest = check_config_version()
print(inspect.signature(cm.run_migrations))          # e.g. (current_ver, results, quiet)
results = {"config_added": [], "config_removed": [], "warnings": [], "errors": []}
cm.run_migrations(cur, results, quiet=True)
# then flatten the config to key→value and diff before/after — that diff IS the authoritative answer
```

Full recipe, measured output and two self-test bugs are disclosed in
`references/execution-driven-upgrade-verification.md`. Probe:
`scripts/probe-config-migration-survival.py` (single file, read-only against production config, automatically
includes the "preset nothing" control case).

**Three companions that must be checked:**

1. **Which non-config files does the migration rewrite?** One measured `migrate_config()` called
   `sanitize_env_file()` as its first step and **atomically rewrote the app's `.env`** (normalizing line format
   only). The backup list must include `.env`, and the plan must diff it afterwards.
2. **`_rewrite_stale_default`-style steps rewrite keys the user *deliberately* set to the old default.**
   Their predicate is `current value == old default`, which cannot distinguish "never set" from "intentionally set".
   One measured run silently changed an archival window from 90 to 30 days. **Copy the current value of every
   business-relevant key into the plan before upgrading, and diff after.**
3. **Version stamping and rollback**: the migration *runner* usually does not stamp the version, the config
   migrator does. Old code encountering a *higher* version number is typically not rejected and executes no steps,
   then rewrites the stamp back to its own latest ⇒ **rollback needs no manual version-number surgery**, but you
   still restore the config backup to undo rewritten keys.

### 10.1 When to write a value (before vs after the upgrade)

Because the predicate is "current value == old default", the correct *timing* of a config write follows from the
final value you want:

| Final value | Write it | Why |
|---|---|---|
| **≠ old default** (want 4, old default 3) | **before** the upgrade (takes effect immediately and survives) | predicate does not match ⇒ the step skips that key |
| **== old default** (want 30, old default 30) | **only after** the upgrade | writing before is a no-op: the migration rewrites it anyway. **This error is invisible afterwards** — the command succeeded and reads back correctly |

Measured with an isolated `APP_HOME` and the *target version's* code: preset `4` → survived; preset `31/91` →
survived; **preset nothing → all 6 keys rewritten**. So the plan must have **two separate action lists**,
"before the upgrade" and "after the upgrade".

Semantics reference (three caveats: predicate-style `match=` steps, `new=None` steps that only delete a key,
and why you must judge by key-level diff rather than by version stamp) →
`references/config-migration-rewrite-semantics.md`.

> When planning "which keys to change back", first compare each key's current value against the **current
> version's default** (in both the defaults table *and* the fallback defaults in code). If all of them equal the
> current default, there is no evidence the user ever chose them deliberately — say "freeze the behaviour on the
> old default", **not** "restore your preference". And do not call an upstream default change arbitrary: the
> migration comment usually states the reason, and that reason is what the user needs in order to decide.

### 10.2 After changing config: does anything apply it automatically?

Writing a value to disk is not the same as it taking effect. For every "change config" step, answer:
**who reads this value, and at which moment?**

```bash
grep -rln 'CONFIG_KEY' RUNTIME_DIR/     # is there a watcher on the runtime side?
```

| Finding | Meaning | The plan must state |
|---|---|---|
| runtime side **has** a watcher (mtime comparison + reload) | applies by itself | the delay and the trigger condition |
| runtime side has **no** watcher (the flag exists only in the CLI) | will **not** apply; the process keeps the old value | the exact explicit reload action, marked **non-optional** |
| value is re-read on every request | applies on the next message | state the criterion, not "should apply" |
| value is read once at construction | the process **must restart** | which process, and how |

Distinguishing the last two is a reading question, not a feeling: if the read site is inside the per-request
assembly path (e.g. a dynamic schema override invoked on every definition call), verify by calling that function
and inspecting the rendered text; if it appears only in construction/init code, it is a snapshot and needs a restart.
This class is the most deceptive: the command succeeds and reading the config back shows the new value while the
running process still uses the old one.

If a reload is not free (one real reload rebuilds the agent/prompt cache and invalidates provider caches),
**quantify that cost** and let the user decide — do not describe it as a casual one-liner.

**Verify that changing one nested key does not change its siblings** — this is measurable, not a matter of opinion:

```bash
mkdir -p /tmp/probe-home && cp ~/.myapp/config.yaml /tmp/probe-home/config.yaml   # read-only copy
APP_HOME=/tmp/probe-home app config set a.b.c VALUE
# flatten both configs to key→value and diff key by key
```

Criterion: **exactly one key changed**, and sibling keys (`args`/`env`/`timeout`) plus all other blocks with the
same name are preserved. Every "change it back" operation in a plan should be rehearsed this way first.

---

## 11. Verify

| Check | Idea | Expected |
|---|---|---|
| Version | `SELECT version()` / `--version` | the target version |
| Row counts | `SELECT count() FROM KEY_TABLE` | equal to the pre-upgrade value |
| Background work | `SELECT count() FROM system.merges` | 0 or a small number |
| Parts count | active parts per table | ≤ pre-upgrade |
| Logs | `docker logs --tail 50` | no corruption/error signatures |
| Dependent API | `curl -s http://localhost:PORT/api/health` | 200 |
| **The original problem** | reproduce the bug you upgraded to fix | fixed — *this is the real acceptance test* |
| Client call paths | every method the host actually calls | all pass (section 5) |

**Errors after the upgrade are not automatically new bugs.** A newer version often validates more strictly and
surfaces **pre-existing damage** (e.g. checksums catching parts left bad by an earlier unclean shutdown).
Diagnose whether the problem predates the upgrade before declaring the upgrade failed.

**Verify your instrument before you declare FAIL.** Observed in a single session, three shapes of tooling defect:
① the check read a `__version__` attribute that does not exist — a false FAIL about a version that was correct;
② the criterion matched the checker itself (its own filename contained the pattern) — a false "leftover process"
alarm; ③ the directory walker silently skipped dot-directories while counting third-party files as its own,
producing three different totals for one question. Three fixed habits:
read metadata via `importlib.metadata.version(DIST)` instead of trusting module attributes;
exclude the checker itself from process/file criteria; count with explicit exclusions
(`.venv`, `site-packages`, `.archive`) and cross-check against a product-provided counter.
Before announcing a failure, ask: *could a correct implementation pass this criterion?*

---

## 12. Roll back

```
1. STOP:    docker compose stop DEPENDENTS COMPONENT
2. WIPE:    clear the volume (only after the backup checksum was verified)
3. RESTORE: extract the verified archive
4. REVERT:  docker-compose.yml image tag back to the previous version
5. START:   docker compose up -d COMPONENT DEPENDENTS
6. VERIFY:  the same table as section 11, plus row counts vs the pre-upgrade numbers
```

Git-deployed variant: `git checkout PRE_UPGRADE_COMMIT` + rebuild the venv + restart; if the schema did not
change (proved in 2.4) there is no data-layer risk. Keep the backup for a few days after a successful upgrade,
then remove it deliberately.

Rollback must also cover the **config layer**: restore the config backup so rewritten keys are undone
(the version stamp itself usually needs no surgery — see 10.3).

---

## 13. Pitfalls

| Pitfall | Symptom | Prevention |
|---|---|---|
| `:latest` tag | an unplanned future upgrade to an incompatible version | pin the exact version |
| Breaking changes not reviewed | fails to start, or data unreadable | read every intermediate release's incompatible-changes section |
| UID changed → permission denied | container starts but cannot write data | compare `Config.User` between versions |
| Dependents retry-storm while the target is down | log flood, unstable dependents | stop dependents *before* the target |
| Giving up because the pull timed out | misread as "image does not exist" | check the mirror, retry with another source |
| tar backup of a large dataset | backup times out or fills the disk | volume snapshot / DB dump above ~100 MB |
| `git fetch --dry-run`, then reading `origin/BRANCH` | stale content ⇒ "upstream has not fixed it" | dry-run does not move remote refs; do a real fetch and confirm with `git rev-parse` |
| A stale git lock blocking fetch, misdiagnosed as network | `fatal: Unable to create '.../shallow.lock': File exists.` while `git ls-remote` works | ① `find .git -name '*.lock'` ② `pgrep -af git` (empty ⇒ stale, not concurrent) ③ read the *first* line of the fetch error. Lock removal is destructive ⇒ follow your destructive-action policy; the lock's loss is 0 bytes. Verify with **both** criteria: fetch exit status **and** `git rev-parse origin/BRANCH` actually moved |
| Assuming a proxy exists | the link is slow/blocked and there is no fallback | enumerate the actual egress options (env, git config, local proxy ports, SSH keys) before promising a workaround. A retry loop with explicit `-c` thresholds and an explicit refspec is the honest mitigation |
| "3ms resolve" treated as feasibility | the install fails later | a real `uv sync` in an isolated worktree |
| Server upgraded, client SDK unchecked | server healthy, dependents call-fail | read the compatibility table; check the installed client version |
| Defaulting to the newest tag | hits a runtime/SDK floor change | cross the three constraints, take the smallest satisfying tag |
| Version bumped but the fix-enabling flag not set | upstream turns a hard error into a graceful no-op: quiet logs, broken feature — **harder to notice than before** | bind the version change and the flag to one atomic action; rehearse A/B (with and without) |
| Pre-flight proving only "starts + health check" | false negative: process up, original bug unfixed | pre-flight must reproduce the fixed bug *and* exercise the client's full call paths |
| Using the production database as the rehearsal target | damage to production | create a uniquely named scratch copy; production is read-only (export only) |
| A patch stored before the upgrade fails to apply after it | line-number drift | do not force `-C` offsets; insert at a known anchor and verify with the two-`--numstat` criterion |
| Inferring environment variable names from class fields | nested prefixes silently wrong yet still "working" | the upstream `.env.template` is authoritative |
| Backup contains only data | config and out-of-lockfile packages lost | include config, `.env`, packages, untracked files, manifest |
| A restore script that only prints PASS | silently unverifiable restore | two-way self-test; copy and verdict are separate stages |
| The tool's check path has guards the apply path lacks | a bare traceback instead of a friendly error | read the apply path when judging whether an option accepts a value |
| Trusting the product's self-reported "upstream version" | stale cached value, off by dozens of tags | trust `git fetch --tags` + `git ls-remote --tags` |
| Sidecars sharing the host venv | `ModuleNotFoundError` after a major SDK bump | isolate each sidecar in its own venv with an upper bound |
| Reproducing "some failure you can produce" instead of the user's actual error | wrong root cause, wrong fix | read the source to enumerate *all* possible messages and their branch conditions; reproduce on the command the user actually ran; if you cannot see their terminal, say so and give them a way to capture it |

---

## 14. Plan output format

An upgrade plan must contain these five sections, in this order:

1. **Current state** — version, data volume, configuration, dependents.
2. **Benefit analysis** — what each intermediate release fixes/improves, with counts.
3. **Risk assessment** — the matrix, item-by-item analysis, citations to known issues.
4. **Execution plan** — the command-level sequence, including the maintenance window and the
   **before-upgrade / after-upgrade config action lists**.
5. **Rollback plan** — the full recovery sequence, including the config layer.

No jumping to conclusions: every claim needs physical evidence — a changelog link, an issue number, a measured value.

---

## References

- `references/case-study-clickhouse-upgrade.md` — a complete same-major upgrade (26.5 → 26.7): assessment, plan,
  execution, verification, and the dependent-application compatibility analysis.
- `references/platform-self-upgrade-mechanics.md` — what a platform's own self-update command actually does,
  stage by stage (pre-flight against bricking, autostash behaviour, config migration run by the updater,
  automatic service restart), the two hard constraints that force external execution, the untracked-file failure
  handling and its read-only-packfile trigger, the pre-upgrade gate checklist, and per-object rollback map.
- `references/git-hosted-app-upgrade-recon.md` — the read-only reconnaissance methodology before upgrading a
  "host-type" component (a platform that is itself an agent/long-running process): tag↔semver matrix,
  config-schema version diff and migration-registry extraction, venv∖lockfile difference,
  untracked-file risk, private-API scan of your own scripts, the "middle major" trap, and which git commands
  remain trustworthy in a shallow clone.
- `references/execution-driven-upgrade-verification.md` — how to *prove* an upgrade claim:
  wheel file-table evidence (always with an old-version control sample), running the migration engine for real
  against a copy, whether a dependency pin actually lands (follow the extras chain), and venv identity via
  `sys.prefix != sys.base_prefix` rather than `realpath`.
- `references/config-migration-rewrite-semantics.md` — the rewrite semantics behind section 10, with the three
  caveats and the measured key-level diffs.
- `references/backup-and-restore-verification.md` — the three backup rules, packaging from `dist-info/RECORD`,
  the verification trio including the negative test, the four restore-script traps, and offline
  reinstallability.

### Scripts

- `scripts/recon-upgrade-surface.sh` — runs the whole read-only reconnaissance for a git-deployed component
  (pass the current and target tags). Entirely read-only; print it section by section rather than piping to `head`.
- `scripts/fetch-release-notes.py` — pulls the GitHub release notes for a list of tags into local `.md` files
  (avoids shell quoting/redirection pitfalls; locate sections with a search tool, then page through them).
- `scripts/probe-wheel-module-presence.py` — falsifies "module X was removed in the target version" by listing the
  file tables of two wheels. **Always include the old version as a control sample**: if the control also fails,
  the probe is broken and proves nothing.
- `scripts/probe-config-migration-survival.py` — measures which config keys survive the target version's migration,
  including the "preset nothing" control case.
