# Read-only pre-upgrade reconnaissance for host-type components (git clone + venv + systemd)

**Applies to**: the component being upgraded **is itself the host** — it is a long-lived process, owns a shared venv, spawns other child processes,
manages its own config + migration system, and **your own scripts/plugins run inside its environment**.
Typical: an agent platform, a self-hosted gateway, a task scheduler. This page does not apply to Docker-type components.

**The core difference (exactly where a Docker checklist falls short)**:

| Dimension | Docker-type | Host-type |
|---|---|---|
| Data plane | volume / DB dump | the host's own core data files (e.g. SQLite state) + config schema |
| Dependency plane | baked into the image | **the host's shared venv** — an upgrade will change your sidecar's dependencies too |
| Config plane | a mounted config | **the platform's own migration system will actively rewrite your config** (including numeric defaults) |
| Your own code | does not coexist | your scripts/plugins import the host's **private API** |

**Discipline: the entire reconnaissance phase is read-only**. Do not `git checkout` / `git reset` / `uv sync` / restart services.
The upgrade action itself comes after the user confirms.

---

## Step 0 — first confirm you are really assessing a "host"

If two of these three hold, follow this page: in `systemctl --user cat <unit>` the `ExecStart` points at a venv's python;
`ps -ef` shows several child processes spawned by it; `git status` shows untracked files you yourself placed there.

---

## Step 1 — tag ↔ semantic-version matrix (do not trust the built-in "upstream version" notice)

```bash
cd <install_dir>
git fetch --tags origin                    # only touches refs, not the worktree; may be slow from some regions, allow a generous timeout
git tag --sort=-creatordate | head -15     # the real latest upstream tags
git rev-parse HEAD; git rev-parse origin/main
```

Then build the matrix tag by tag (**run it as a script, not an inline for loop — an inline loop will trip a hard command-parser block**):

```
printf "%-14s %-12s %-14s %-8s %s\n" TAG SEMVER REQUIRES_PY NODE DATE
for t in <tags>; do
  ver=$(git show "$t:pyproject.toml" | sed -n 's/^version *= *"\([^"]*\)".*/\1/p' | head -1)
  py=$(git show "$t:pyproject.toml" | sed -n 's/^requires-python *= *"\([^"]*\)".*/\1/p' | head -1)
  node=$(git show "$t:.nvmrc" | tr -d '\n')
  d=$(git log -1 --format=%ad --date=short "$t")
done
```

Produce a **path table**: each tag's date + semantic version + PR count, and mark out the **major releases and the "patch that fixes a major"**.

> ⚠️ **Trust only the results of `git ls-remote` / `git fetch`.** In a measured run, the component's own update-status line reported an
> upstream commit that **did not match** the real HEAD (off by several dozen tags) — it was a cached value.

> ⚠️ **Compare the Node / Python thresholds against the previous tag**; do not treat "a gap that already existed" as "a threshold added this time".
> Measured example: `.nvmrc` says 26, but `package.json`'s `engines` is `^22.22.0 || ^24.11.0 || >=26.0.0`, and the local 22.22.3 → **satisfied**.
> Looking only at `.nvmrc` would falsely report a risk that does not exist.

---

## Step 2 — config schema version gap + migration registry extraction

```bash
grep -n '_config_version' ~/.myapp/config.yaml          # your currently persisted value
git show <NEW>:<.../config_defaults.py> | grep -n '"_config_version"'   # the target's latest value
git ls-tree -r --name-only <NEW> | grep -i migrat         # where the migration module lives
git show <NEW>:<.../config_migrations.py> | grep -nE '^def _migrate_to_|^MIGRATIONS' 
git show <NEW>:<.../config_migrations.py> | sed -n '/^MIGRATIONS/,/^)/p'
```

**The key move = translate each migration step's semantics into "what it will do to me", and check each one back against your own config**:

```bash
# for each target key the migration touches, check back whether you hit its trigger condition
for k in <keys touched by the migration>; do
  printf "  %-36s %s\n" "$k" "$(grep -nE "^[[:space:]]*${k}:" ~/.myapp/config.yaml || echo '<not set>')"
done
```

The three migration shapes you will actually meet mean completely different things:

1. **`_rewrite_stale_default(old default → new default)`** — your value is changed only if it **equals the old default**.
   This kind is the most dangerous: it is not a bug fix, it **quietly enlarges your numeric value** (in a measured run it changed `max_iterations: 50→250` and
   `max_concurrent_children: 3→10`), and enlarging a cost-type parameter directly costs money.
   It must be listed separately in the report as a "change back immediately after upgrading" checklist.
2. **`_rewrite_key(..., match=lambda cur: cur is not None)`** — a hit deletes the key.
   When your value is empty it is a no-op.
3. **`_migrate_to_N` has a function body** — it may **modify your non-config files**.
   In a measured run one step strips a specific heading section out of some `SOUL.md`. **Before upgrading, use `grep -F` to check whether your own copy has that heading**,
   to confirm whether it is a no-op or will really change something.

Two further levers: the migration system usually has a **support floor** (below some version it no longer auto-migrates) and
a persistence policy of **"only write values that differ from the schema default"** — the former decides whether you get rejected, the latter decides whether your config
gets silently filled with defaults. Read them once before concluding.

---

## Step 3 — dependency plane: venv ∖ lockfile set difference (the "dual-track" extra-installed packages)

Hosts commonly use `uv pip install` to manually add packages that are not in the lockfile (for plugins). A dependency sync wipes them out,
**and the failure is silent** (the plugin raises no error, it simply stops happening).

```bash
SP=<venv>/lib/python3.*/site-packages
ls -d "$SP"/*.dist-info | sed 's#.*/##; s/\.dist-info$//; s/-[0-9][^-]*$//' | tr 'A-Z_' 'a-z-' | sort -u > /tmp/installed.txt
git show <NEW>:uv.lock | grep -oE '^name = "[^"]+"' | sed 's/name = "//; s/"//' | tr 'A-Z_' 'a-z-' | sort -u > /tmp/lock.txt
comm -23 /tmp/installed.txt /tmp/lock.txt      # in venv but not in lock = will be wiped out
```

Then check the **old vs new version** of the same batch of key SDKs (this is the real entrance for breaking changes):

```bash
for pkg in <key packages>; do
  o=$(git show <OLD>:uv.lock | grep -A2 "^name = \"${pkg}\"$" | sed -n 's/^version = "\(.*\)"/\1/p' | head -1)
  n=$(git show <NEW>:uv.lock | grep -A2 "^name = \"${pkg}\"$" | sed -n 's/^version = "\(.*\)"/\1/p' | head -1)
  printf "  %-20s %-14s -> %-14s %s\n" "$pkg" "${o:-absent}" "${n:-absent}" "$([ "${o:-x}" != "${n:-y}" ] && echo '  <== CHANGED')"
done
```

**While doing this diff, take every major jump to the upstream changelog** — the breaking changes of a large version jump
almost always land here (measured: `mcp 1.28.1 → 2.0.0`).

---

## Step 4 — files you brought into the host (untracked / uncommitted)

```bash
git status --porcelain          # `??` = untracked (local plugins/config); ` M` = modified tracked file
git branch -vv                  # are you on a detached HEAD, how far behind is the main branch
```

Three things must be clarified:

1. **How the upgrade flow handles them**: grep the target version's updater code for
   `git stash push --include-untracked` / `reset --hard` / `git clean`.
   In a measured run the flow was stash → reset → restore, **on conflict it leaves them in the stash and prints the ref** (the on-disk files disappear, the data is not lost).
   → `cp -a` a copy beforehand is the cheapest insurance.
2. **Whether the upstream version will overwrite them**: `git cat-file -e <NEW>:<path>` / `git ls-tree -r <NEW> -- <dir>`.
   **Absent** in the target version = safe (reset will not overwrite); **present** = conflict.
3. **Whether a detached HEAD blocks the upgrade**: read the updater's parked-branch predicate function.
   In a measured run the criterion had only two conditions — **clean tree** + **no `+` lines from `git cherry origin/<target>`**.
   If both pass it switches cleanly to the main branch; `git merge-base --is-ancestor HEAD origin/<target>` can corroborate.
   → Do not treat "I am parked on a detached HEAD" as a blocker; look at the criterion.

---

## Step 5 — whether your own scripts/plugins import the host's private API

```bash
grep -rnE '^\s*(from|import)\s+(<app>_|cron|tools|agent|gateway|<app>_cli|run_agent|model_tools|toolsets)' \
  ~/.myapp/scripts/ 2>/dev/null
```

Cross-reference the hits against **the refactored-file list from Step 2** — for the files the target version changed, use `git diff --numstat <OLD> <NEW> -- <file>`
to see added/removed line counts (a deletion on the order of tens of thousands of lines = that module was split up, and any private symbol depending on it will break).

> Long-term fix: rewrite such scripts to "read a config file / read a data file" rather than importing the host's private symbols.
> Private functions (starting with `_`) are especially fragile — they are not an API.

---

## Step 6 — the right way to read release notes

1. Use `scripts/fetch-release-notes.py` to pull the notes of **every tag** in the window into local `.md` files
   (this avoids the approval gates on `python3 -c` and `>` redirection).
2. Do not force-read a large body: use `search_files` first to locate section keywords
   (`breaking|migrat|deprecat|no longer|removed|⚠️|config version|must now`), then `read_file` with paging.
3. Patch releases usually have **no curated notes**, they only roll up a PR count — to study "what got upgraded" you must
   go back to the **previous major's** curated notes, which cover the whole intervening patch window.
4. **Look specifically for sentences that admit a problem was introduced in that release** — this is the only source of the criterion for "do not stop between majors".
5. Finally, order the upgrade items by "value to **this particular user's concrete assets**", not by copying upstream's Highlights.
   The criterion = the failure classes the user has already logged / the costs they are watching / the specific integrations they use.

---

## Step 7 — post-upgrade verification checklist (template)

```
□ component version + actual key SDK versions (ls -d <venv>/lib/python3.*/site-packages/<pkg>-*.dist-info)
□ config migration result (<component> config check) + change the enlarged numeric parameters back to their original values
□ extra-installed packages still present (import each one)
□ exercise each sidecar / integration individually (not just pinging a health check)
□ scheduled jobs' next_run_at advances normally (watch a natural trigger, do not use "run now" to verify — see below)
□ run each self-built script (the Step 5 list)
□ whether new write-approval gates block automation paths (you must test this; reading the docs cannot decide it)
```

### One counter-intuitive pitfall found in real testing

**"Run now" consumes the sole quota of a one-shot task.** The scheduler implements a manual run as
"set `next_run_at=now` → go through the normal fire → `mark_job_run` increments completed → on reaching the limit it
retires to terminal". So using `action=run` to "verify whether a one-shot job can still run"
**permanently disables it once the verification completes**. Physical evidence from a measured run: two one-shot jobs ended up with `repeat={'times': 1, 'completed': 1}`
+ `enabled=false` + `next_run_at=None`.

→ **Rule**: for verification, always run scripts directly with `bash <script>`; only use "run now" on a schedule when you "really mean to spend that one shot".
(This has survived several major versions unchanged; do not expect an upgrade to fix it.)

---

## Which git commands are trustworthy under a shallow clone

Host installs are often `--depth 1` (a non-empty `.git/shallow`) or partially fetched. Measured:

| Command | Trustworthy? | Notes |
|---|:---:|---|
| `git diff <A> <B>` / `git diff --numstat` | ✅ | it compares the two **trees**, independent of history completeness |
| `git show <tag>:<path>` | ✅ | reads that tag's blob directly, the best option |
| `git cat-file -e` / `git ls-tree -r --name-only` | ✅ | same as above |
| `git merge-base --is-ancestor A B` | ✅ | usable in practice (outside the shallow boundary it degrades to undecidable, so recheck) |
| `git rev-list --count A..B` | ❌ | the shallow boundary makes it return an **absurdly huge value** (measured: 37,632 commits / 5 weeks). For a commit count, consult the upstream API or the official number in the release notes |
| `git log --since` / `git describe` | ⚠️ | depends on history depth; treat conclusions as hints only |

**`git fetch --tags` can obtain all tag objects** even when the repo is shallow — so the "compare pyproject / uv.lock tag by tag"
route works on a shallow clone; do not give up just because it is shallow.
