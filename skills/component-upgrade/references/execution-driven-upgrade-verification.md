# Execution-driven verification

**Where it applies**: in upgrade assessment / version-compatibility judgment, whenever you face
**second-hand statements** such as "upstream says X changed", "the pin is Y", "the registry has N
steps", do not take them at face value — run the artifact and let it speak for itself in an isolated
environment.

**Why it is needed**: static reading (release-notes prose, pyproject pins, grepping the registry) is
**confirmation-oriented**; it naturally finds only evidence that supports the existing hypothesis.
Execution-driven work is **falsification-oriented**: you actively construct samples that could overturn
the conclusion.

The four items below are measured takeaways; each one gives "wrong approach → right approach → measured
sample".

---

## 1. "Does this module still exist in the new version?" — list the wheel's file table

**Wrong approach**: read the release notes ("the old import path is gone rather than deprecated") plus
the `pyproject.toml` pin, and conclude from those.
**Problem**: the docs are prose, and the pin only says "what it intends to install"; **neither equals
"the artifact really does not contain it"**.

**Right approach**: fetch the wheel from PyPI and list its file table directly.

```python
# a probe under scripts/, or copy this verbatim
import io, json, urllib.request, zipfile
meta = json.loads(urllib.request.urlopen(
    "https://pypi.org/pypi/<pkg>/<version>/json").read())
w = [u for u in meta["urls"] if u["filename"].endswith(".whl")][0]
names = zipfile.ZipFile(io.BytesIO(urllib.request.urlopen(w["url"]).read())).namelist()
print(any(n.startswith("pkg/sub/module/") for n in names))
```

**You must have a control sample**: first run an old version where the module is known to exist. If that
control also reports "absent", **the probe itself is broken** and cannot be used as evidence.

**Measured sample**: in the mcp 1.28.1 wheel, `mcp/server/fastmcp/` has **19** entries and
`mcp/server/mcpserver/` has **0**; mcp 2.0.0 is the reverse (fastmcp **0**, mcpserver **21**).
⇒ A self-built MCP server that depends on `from mcp.server.fastmcp import FastMCP` **will certainly**
raise `ModuleNotFoundError`. This upgrades the claim from "the docs say it will" to "the artifact really
does not contain it".

---

## 2. Config migration: actually run it, do not read the registry

**Wrong approach**: `grep '^def _migrate_to_'` + reading the registry locally.
**Why it is wrong**: the registry contains both named functions and **inline factories**
(`functools.partial(_rewrite_key, …)`, `_rewrite_stale_default(...)`), and **the same version number can
appear in more than one entry**. Scanning only the named functions misses whole blocks. Measured: the
app's migration 44 has **two same-named `(44, _rewrite_stale_default(...))` entries**; missing one means
missing two key changes.

**Right approach**: an isolated environment + a **copy** of the real config, and run the target version's
migration engine.

```bash
# ① target-version code: git worktree (does not touch the live checkout)
git -C <repo> worktree add --detach /tmp/probe <target_tag>

# ② fake APP_HOME + a copy of the real config (the copy is essential: migrations write to disk)
mkdir -p /tmp/probe-home && cp ~/.myapp/config.yaml /tmp/probe-home/config.yaml
```

```python
# ③ bind the real signature before calling (do not guess arity; signatures differ between versions)
import inspect
from myapp.config import check_config_version
from myapp import config_migrations as cm
cur, latest = check_config_version()
print(inspect.signature(cm.run_migrations))      # measured: (current_ver, results, quiet)
results = {"config_added": [], "config_removed": [], "warnings": [], "errors": []}
cm.run_migrations(cur, results, quiet=True)
```

```bash
# ④ run it in the isolated environment
PROBE_HOME=/tmp/probe-home APP_HOME=/tmp/probe-home \
  <venv>/bin/python /tmp/probe.py
```

**Reading the output**: compute a **flattened key→value diff** of the config, before vs after.
`run_migrations()` **does not stamp the version** (that is `migrate_config()`'s job, see below), so only
the rewritten keys show up in the diff.

**Measured sample (the app v0.20.1 → v0.21.4, `_config_version` 34 → 45)**: reading the registry
suggested "4 keys changed"; actually running it produced **6**; the two that were missed were
`curator.stale_after_days 30→14` and `curator.archive_after_days 90→30`.

### 2b. Three things to check alongside

| What to check | Why | Measured conclusion |
|---|---|---|
| whether the migration engine also writes **non-config files** | the backup list must cover them | the first step of `migrate_config()` is `sanitize_env_file()`: it **atomically rewrites `~/.myapp/.env`** (it only normalizes the line format, it does not touch values) |
| the **matching semantics** of `_rewrite_stale_default` | decides "will a config I never touched get rewritten?" | the predicate is `current value == old default` ⇒ **it cannot tell "never set" apart from "deliberately set to the old default"**; both get rewritten |
| who stamps the version, and whether rollback gets rejected | decides rollback difficulty | `run_migrations` does not stamp; `migrate_config()` does. Old code that meets a "higher version number" **is not rejected by the floor gate** (the gate's predicate is `< 12`) and executes no step, then writes the stamp back to its own latest ⇒ **rollback needs no manual version edit**, but restoring the config backup is still necessary (to undo the rewritten keys) |

---

## 3. Will a dependency pin actually land — chase the extras chain

`pyproject.toml` / `uv.lock` record "what is in the lockfile"; they **do not answer "will it be installed
at install time"**.

The chain must be chased all the way to the installer:

```
install.sh uses `uv sync --extra all --locked`
        ↓
pyproject's `all = ["myapp[cron]", "myapp[mcp]", ...]`
        ↓
`mcp = ["mcp==2.0.0", ...]`
        ↓
⇒ the mcp in the host venv will be swapped to 2.0.0
```

**Measured sample**: reading only uv.lock yields "2.0.0 vs the installed 1.28.1; whether it will be
swapped is uncertain"; expanding `all` turns the conclusion into **it will definitely be swapped**. That
difference moves the risk level from "undetermined" to "will definitely break".

**Check alongside**: whether a **third-party / self-built service** runs in the host venv.
Measured: a self-built MCP server's config says `command: python3`, while the gateway's PATH has the host
venv's `bin/` first ⇒ it consumes the **host venv's site-packages**. When a host dependency changes, that
external service breaks with it, and its own `requirements.txt` says `mcp>=1.1.0` (no upper bound, so it
will never block).
→ The mitigation is **isolating it into its own venv** (there is a precedent elsewhere in the same
deployment: another MCP server ships its own `.venv` pinned to the old version and is naturally immune).

The predicate must be verified positively, not just by reading the unit text:

```bash
cat /proc/<pid>/environ | tr '\0' '\n' | grep ^PATH=      # the process's real PATH
<resolved_python> -c "import sys; print(sys.prefix != sys.base_prefix, sys.path)"
```

---

## 4. venv identity: do not use realpath

**Wrong approach**: `os.path.realpath("/path/.venv/bin/python3")` and then check whether the path
contains `.venv`.
**Why it is wrong**: a venv's `bin/python3` is a **symlink** to the base CPython; once realpath resolves
it, the venv identity is gone → **false negative** (a measured script printed it as
`is myapp venv python: False`, the opposite of the truth).

**Right predicate**:

```python
sys.prefix != sys.base_prefix        # primary predicate
[p for p in sys.path if "site-packages" in p]   # corroborating evidence: which venv it points to
```

---

## Methodology takeaways (written for next time)

1. **The two rounds must change the direction of reasoning, not switch tools**: round one is "read
   docs/code to find supporting evidence" (confirmation-oriented); round two is "run the artifact,
   construct counterexamples" (falsification-oriented). Another grep at the same layer does not count as
   independent verification.
2. **For every second-hand statement, ask "what command would falsify it"**. If you cannot answer, it is
   an assumption, not a fact.
3. **Only an experiment with a control sample carries evidential weight**. Without a control, a broken
   probe will be read as "this is just how the new version is".
4. **Audit your own probe**: this round's self-check found two script bugs (a realpath false negative;
   `${o:-x}` vs `${n:-y}` classifying `absent → absent` as CHANGED). When the probe output shows a
   "surprising finding", suspect the probe first.
5. **Hand over a side-effect list after running**: this round produced a `git worktree` (220M) plus a fake
   APP_HOME (104K); on a real environment, prove non-interference positively with mtime / `git status`,
   and provide the cleanup commands without running them for the user.
