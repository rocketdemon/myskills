# Verifying backup and restorability before an upgrade

**When to read this**: you are about to back up venv extra-installed packages, untracked files, or config files ahead of an upgrade/migration.
The core question is not "did I make a backup" but **"can this backup actually be restored"** — an unverified backup is merely an assumption.

Origin: pre-upgrade hardening (R2 extra-installed packages + R4 untracked plugins) for an app v0.20.1 → v0.21.4 upgrade.
Both backup classes were exercised in both directions, and the process turned up 1 real bug.

---

## 1. Three iron rules

1. **The backup inventory must come from the source of truth, never from memory**:
   - untracked files → the `??` lines of `git status --porcelain` (git's own determination)
   - extra-installed packages → `on-disk dist-info ∖ lockfile` (set difference, listing every package by name)
   - the contents of a single package → that package's `dist-info/RECORD` (the per-file + hash manifest the installer wrote)
2. **The verification criterion must not be "the tool's exit code"**: `cp` exiting 0 does not guarantee identical contents, and exiting 1 does not mean the contents are corrupt (see §4).
   The only reliable criterion = **a per-file sha256 comparison** (against the MANIFEST written at backup time).
3. **Separate the operation from the criterion**: the copy stage only copies; the verdict comes from an independent verifier. The restore script calls the verifier as its last step.

## 2. Choosing a packaging method

| Object | Method | Rationale |
|---|---|---|
| Python package | `tar -czf -C <site-packages> -T <listfile>` using the file list from `dist-info/RECORD` | RECORD includes `*.libs/`, `_*.so`, and inconsistently named subpackages; **a hand-written glob will miss them** |
| Directory/file | `cp -a` the whole directory (preserves permissions/symlinks) | convenient for a direct diff |

**How to read `RECORD`**: CSV, `path,hash,size`; `hash` looks like `sha256=<urlsafe-b64 without padding>`.
**RECORD's own line has an empty hash** ⇒ "lines with a hash = total lines − 1"; when verifying, do not count RECORD itself as a mismatch.

## 3. The three-part verification suite (all three required)

1. **Verify immediately after packaging**: unpack to a temp directory → compare every file's sha256 against RECORD.
   Criterion: **all** hash-bearing lines match (one measured run: bcrypt 10/10, OBSERVABILITY_APP 551/551, lxml 175/175,
   python-docx 125/125 = 861/861).
2. **Actually run the restore script in a sandbox**: see §5 (the parameterized-copy technique).
3. **Two-way self-test**: a positive case + **a negative case**. The negative case is not optional — **a verifier that only ever says PASS is no verifier at all**.

| Case | Construction | Required |
|---|---|---|
| Positive 1 | run the restore onto an empty target directory | everything lands, exit code 0, PASS |
| Positive 2 | **immediately run it again** | idempotent, still exit code 0, still PASS (specifically exercises overwriting read-only files, see §4) |
| Negative 1 | delete one file from the backup | verifier reports FAIL, exit code non-zero |
| Negative 2 | append **one byte** to a file | verifier reports FAIL, exit code non-zero |
| Cleanup | revert the change and re-run the restore | back to PASS |

## 4. The four pitfalls of a restore script (all hit in real testing)

| Pitfall | Symptom | Fix |
|---|---|---|
| `cp -a srcdir $DEST/` nests | target already has a directory of the same name → becomes `srcdir/srcdir` | for directories use `cp -a "$SRC/{p}/." "$DEST/{p}/"` (copy the **contents**), and branch into two forms for directories vs files |
| **Overwriting a read-only file is refused** | a backup may hide 444 files (**a packfile of an embedded git repo** is one). `cp -a` copies the 444 bit too ⇒ a second run fails to overwrite the read-only file with `Permission denied`, exit code 1, **even though the data is actually fine** | `cp -a --remove-destination` (delete the target before copying) |
| Using the exit code as the restore criterion | the "false FAILED" above misleads the user into thinking the restore failed, and then into manual fiddling | switch the criterion to a per-file hash comparison |
| Read-only files when reinstalling packages | `tar` by default may not be able to overwrite 444 files | first check "how many read-only files are in the target package": `find <pkg> -type f ! -perm -u+w \| wc -l`; in practice pip-installed packages usually have **0**, so no special handling is needed |

## 5. Sandbox testing with the "parameterized copy" technique

A restore script usually hard-codes its target paths (`SITE=` / `REPO=`). **Do not** edit the script itself just to test it (then you are no longer testing the artifact).
Instead: read the original → replace only that one line → write a copy → run the copy in the sandbox.

```python
new, n = re.subn(r'^REPO=".*"$', f'REPO="{SANDBOX}"', txt, count=1, flags=re.M)
assert n == 1, "path line was not substituted, the test is invalid"   # ← must assert, otherwise the test may have tested nothing
```

**Be honest in labeling**: state in the report that "what was tested is a parameterized copy, differing only in the path constant",
and explain which failures are expected sandbox failures (e.g. the `import` acceptance block at the end of the script necessarily errors out against a fake target).

## 6. Check offline reinstallability up front

**Do not assume the package manager's cache still holds wheels.** In a measured run, uv unpacked but **kept no wheel archives**
(in a 2.9G `~/.cache/uv`, the wheel count for `bcrypt`/`OBSERVABILITY_APP`/`lxml`/`python_docx` = **0**,
only `archive-v0` unpacked directories) ⇒ without a network you cannot reinstall from the cache, **this tar.gz is the only fallback**.

How to check: `find "$(uv cache dir)" -name '<pkg>-<ver>*.whl' | wc -l` (use `find -name`;
**do not use Python `os.walk`** — the cache directory has an enormous number of files, it will time out; and before timing out stdout is buffered, so the whole batch of output is lost).

**Two low-cost facts while you are here**:
- `uv pip freeze --python <venv>` saved as a separate **full inventory** for version comparison (one measured run: 128 packages).
  It complements the "extra-installed packages tar": the tar handles **being able to reinstall them**, freeze handles **being able to diff which packages/versions changed**.
- **`uv` has no `pip download` subcommand** (`uv pip` only has compile/sync/install/uninstall/freeze/list/
  show/tree/check), so the "download wheels first to build a wheelhouse, then install offline" route **does not work** under uv;
  either use real pip, or fall back on that tar archive above.

## 7. When the criterion is wrong, dare to declare FAIL and fix the criterion

In this measured run the first version of the verification script reported `FAIL` (all 4 packages MISMATCH). Digging in revealed that **I had written the criterion wrong**:
it counted only the files under `<pkg-name>/`, missing `<pkg-name>-<version>.dist-info/`; adding them gave exactly the RECORD line count.

Rule: **when the output is FAIL, do not first suspect the object under test, and do not just wave it through by changing the criterion to "make it pass"**.
First distinguish "the backup really is broken" from "the criterion is wrong"; after fixing to the correct criterion, **re-run** and draw the conclusion from the new evidence.
The negative-case test (§3) exists precisely so that this kind of criterion error cannot slip past unnoticed.

## 8. Companion: will the target version overwrite your untracked files?

```bash
git ls-tree -r <target_tag> --name-only -- <path>
```

Empty = that path is not tracked in the target version ⇒ `reset --hard` **will not** overwrite it, and the only remaining risk is a stash/pop conflict.
In a measured run all 4 untracked paths came back empty, which removed the "overwritten by reset" risk entirely (lighter than the earlier worry).
(This criterion also appears in the `SKILL.md` pitfalls table, row "untracked / uncommitted files swept up by autostash".)
