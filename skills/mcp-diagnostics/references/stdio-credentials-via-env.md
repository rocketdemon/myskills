# Serving stdio MCP credentials from the env map instead of argv

## What this fixes, and what it does not

A stdio server that takes credentials as flags publishes them on every dump: `ps`, `pgrep -af`, argv-logging in audit/journald, crash dumps, and any harness transcript where you pasted a process list while diagnosing. The credential then lives in places nobody chose to publish it.

Honest boundary — state it whenever you propose this change:

- **Env is not a cryptographic boundary.** `/proc/<pid>/environ` is readable by the same UID, so a same-user attacker still reads the value.
- **The secret still sits in the harness config file in plaintext** (unless you also move it to the server's own config file — see Step 0).
- The win is concrete but narrow: it removes the **argv surface**, which is the one that leaks into every process dump, log scan and report.

So sell it as "stops the leaking", never as "the secret is now protected".

## Step 0 — Does the server accept env credentials at all?

Grep the installed package before planning anything:

```bash
grep -rEo "process\.env\.[A-Z_]+" <pkg_dir>/dist 2>/dev/null | awk -F: '{print $2}' | sort -u
grep -rniE "environment variable|APP_SECRET|APP_ID" <pkg_dir>/README.md | head -12
```

Node MCP servers commonly read `<APP>_ID` / `<APP>_SECRET` as fallbacks for their `-a` / `-s` flags (the README usually says so explicitly: "no need to specify -a and -s"). Confirm the exact variable names — they are the whole fix.

If the server has **no** env path, fall back in this order:

1. its own config-file flag (`--config /path/to/600.json`): the path stays in argv, the secret does not, and only this server reads the file;
2. a wrapper script that exports the values then `exec`s the server (last resort: an extra moving part, and any hard-coded path inside it drifts — a cached-package binary path in particular changes under you).

## Step 1 — Confirm the harness forwards a per-server env map

Hermes merges the server's own `env` into the stdio child: `tools/mcp_tool_config.py::_build_safe_env` applies `env.update(user_env)` last, and `tools/mcp_tool_transport.py` passes the result as `StdioServerParameters(env=…)`. Config shape:

```yaml
mcp_servers:
  <server>:
    command: <launcher>
    args: [...]
    env:
      APP_ID: <value>
      APP_SECRET: <value>
```

**Do not plan on the harness's own secret plumbing instead.** `hermes_cli/env_loader.py::secret_source_names()` returns only names supplied by an **external secret source** (password-manager backends) — a key that merely exists in `.env` is *not* injected into stdio children. Verified by reading the loader, not assumed from the docs.

## Step 2 — Sandbox the edit before touching the live file

`hermes config set` refuses an unknown path under a known section unless `--force`, and list edits are index-based (a wrong index silently corrupts the server's launch), so prove the exact commands against a throwaway home first:

```bash
SB="$TMPDIR/sandbox-hermes-$$"          # scratch dir, NOT the live hermes home
mkdir -p "$SB" && cp ~/.hermes/config.yaml "$SB/config.yaml"
HERMES_HOME="$SB" hermes config set mcp_servers.<server>.env.APP_ID placeholder
HERMES_HOME="$SB" hermes config set mcp_servers.<server>.env.APP_SECRET placeholder
HERMES_HOME="$SB" hermes config unset mcp_servers.<server>.args.<idx>
wc -l "$SB/config.yaml"; md5sum ~/.hermes/config.yaml    # live file must be unchanged
```

Verified behaviour worth knowing: `set mcp_servers.<server>.env.<KEY>` succeeds **without** `--force`; the CLI masks the value it echoes back; `unset ...args.<idx>` deletes one existing item (element count −1).

## Step 3 — The live edit: two halves, both required

1. **Add the `env` map.** Read the current values out of the existing config *inside the script* and pass them straight to `hermes config set` — never re-type them, never echo them, so the value never enters your output or the transcript. The CLI's own echo is masked.
2. **Delete the credential flags *and* their values from `args`.** Deleting the **same index repeatedly** removes a run of items deterministically, because each delete shifts the remainder left:

```bash
for i in 1 2 3 4; do hermes config unset mcp_servers.<server>.args.<first_idx>; done
```

Stopping after step 1 defeats the point: explicit args win over env, so the credential still reaches argv.

## Step 4 — Check it (four judgements, in this order)

| # | Judgement | Evidence |
|---|---|---|
| 1 | structure moved, nothing else did | structure-only dump (keys/line numbers), `args` element count down by exactly the number of removed flags, `diff` against a pre-change backup |
| 2 | the server really honours env credentials | `hermes mcp test <server>` → `Connected` + the **expected tool count**. It spawns from a freshly-read config, so it proves the env path *before* any restart |
| 3 | live entry re-created | a transport reconnect reuses the **cached** `command`/`args`, so nothing changes for the running server until `/reload-mcp` or a gateway restart |
| 4 | after the restart | process args carry no credential flags, tool count unchanged, one real read-only call returns data |

A handshake that succeeds with the flags still in `args` proves nothing — env values are fallbacks, so a server that reads its flags happily works either way.

**Two probe traps that both fired on a real run:**

- **`pgrep`/`ps` will hand you the OLD process.** When the previous connection (old flags, same server name) is still alive, a naive `pgrep -n -f <server>` returns *it*, so `/proc/<pid>/environ` shows no `APP_ID` and you report "env did not take effect" — a false alarm. Snapshot the pid set **before** spawning, then inspect only pids that appeared during the run; if none is caught while it lives, record **undetermined**, not failed.
- **One timing sample is not a judgement.** The same `hermes mcp test` measured 6.0 / 24.5 / 45.1 / 14.2 s across four runs (high values on both sides of the change). Concluding "my edit made it slow" from a single 45 s reading is wrong; judge by structural evidence (resolved command line, presence/absence of an `npx` wrapper process) or fix the variables and take several samples. Server-side is the cleaner datum: gateway spawn → registered took ~14 s *including* queueing behind four other servers.

**Don't propose deleting a same-value line you have not traced.** A value-identical sweep hit is not automatically a duplicate copy: one app credential can legitimately serve two consumers. Here `FEISHU_APP_ID`/`FEISHU_APP_SECRET` in `.env` are the **messaging-channel** credentials (`gateway/config_env.py` platform map), while the MCP server reads `APP_ID`/`APP_SECRET` from its config `env` map — same app, two consumers. `.env` keys are *not* injected into stdio children (`_SAFE_ENV_KEYS` is PATH/HOME/… only), so they are not a redundant copy of the env-map path. Name the consumer of every hit before proposing removal: deleting the channel's line breaks the bot.

## Step 5 — Sweep for copies the argv exposure already created

The secret has been in argv, so it may already be in logs, backups, archives and transcripts. Sweep with **names and counts only**:

```bash
grep -rlF "$SECRET" ~/.hermes --exclude-dir=.venv    # file names only
grep -rcF "$SECRET" <each hit>                       # per-file counts, never the lines
```

Report which files hold it and what you propose for each (rotate, redact, move, delete) — printing a matching line re-publishes the secret you are trying to clean up.

**Quote a total, and never `head` the category breakdown.** A per-directory summary piped through `head -20` looks complete while silently dropping the biggest category: the harness's own session transcripts (`sessions/*.json`) plus the session DB sorted *below* the visible lines and the reported blast radius came out at 96 files when the real count was 270. Count every category with full output and assert the sum equals the file total before quoting a number — a truncated breakdown fails exactly like an unrun check. The re-runnable form of this sweep is `scripts/sweep-secret-copies.sh <config-key-path> [root]`.

**Scope the sweep to the transcript stores, not just backups.** Backups are the obvious hits; the session DB and per-session JSON hold whatever you pasted into a diagnostic, and unlike a backup they are **re-sent to the model provider on later turns** — a genuine off-machine path that local cleanup does not retract. Say that when sizing the risk instead of reassuring the user with "the copies are all local".

**When a hit is a script nobody needs any more, retire it — do not rewrite it.** An old one-off that nothing schedules or imports is not worth a refactor to read from config. Move it (`mv -n`, never `rm`) into `data/archive/<topic>-<date>/` and write a `README.md` beside it: origin path, size/lines, md5, why it was retired, the one-line revive command, and who still depends on it. Two things that bite:

- **Carry the compiled cache too.** `__pycache__/*.pyc` holds the same literal bytes; a source-only review reports "clean" while the bytecode copy stays. Move it with the source and record its md5 in the manifest.
- **Grep the import chain before moving.** A sibling's `from <module> import …` becomes a dangling import the moment the module leaves the directory — report it, then either retire the dependent too or leave the module in place.
- **Write the manifest's credential status from what was EXECUTED, not from what you proposed.** A manifest that says "these copies hold an already-invalidated secret" is false the moment the rotation it assumed is deferred or cancelled — and a later reader then treats live credentials as harmless. Record the status the action actually produced, and when a planned remediation is called off, re-read and correct every document that assumed it (a proposal is not a fact).
- **A cancelled rotation leaves every copy LIVE.** The sweep hits, the archive, the backups and the session DB then hold the *current* credential: say so in the manifest, with the two prohibitions that follow (never copy them off-machine, never dump their contents), so nobody "tidies up" by publishing them. Closing the argv surface is what stops the set from *growing*; the existing copies stay a standing risk the user knowingly accepted — do not re-open the rotation proposal without a new exposure path.

## Step 6 — Rotation is the only remedy for copies that already spread

Redacting the files you can find does not reduce exposure, because you cannot enumerate every copy — and the argv surface guarantees some are out of reach. Rotation is what makes every unreachable copy worthless at once. Frame it that way, and state the price before the user agrees; do not sell it as mandatory hygiene if you cannot name the exposure.

**Sequence — order matters, step 3 breaks the old credential instantly:**

1. **Inventory the consumers first.** A rotation that misses one leaves that integration broken. Enumerate and classify: platform/channel credential, each server's config `env` map, any `os.environ.get(..., "<literal>")` fallback, cron jobs, wrapper scripts. Grep for the **value** (which files) and for the **names** (which code) — a fallback literal in a script you never run still consumes the old value.
2. **Prove the write path in a sandbox.** `HERMES_HOME=<copy> hermes config set <NAME> <placeholder>`, then check which file actually changed. Verified behaviour: a bare name registered as an optional env var lands in **`.env`**, while a dotted path under a config section lands in `config.yaml` (the CLI masks the value it echoes). Test with a right-shaped placeholder, never a real value.
3. **Take the new value through a file, never through the chat.** Have the user write it into a `600` drop file; read it into a variable inside the script, pass it straight to `config set`, and print only length/equality/provenance. Same principle as the masking section: the strongest mask is the value never entering the output.
4. **Fail closed on cheap preconditions** so a half-applied rotation cannot happen — drop file missing, empty, wrong length, or identical to the current value → distinct non-zero exit per case, and stop before touching either target.
5. **Back up both target files, and keep the drop file** (moved aside, not deleted) until **both** locations verify equal to the new value.
6. **Only then restart the harness**, and re-verify end to end: a real read-only call through each consumer, no credential in `ps`, no auth errors in the log. Written ≠ rotated; it is done when the new value is proven working.

**Announce the outage window before the user resets.** The second the provider rotates the secret, every consumer still on the old value fails until it is updated and restarted — seconds to minutes if the drop file and the script are already prepared. Prepare first, rotate second.

## The masking rule this file exists to enforce

A mask written for one shape silently passes the others. `sed 's/:\(.*\)$/: <hidden>/'` handles `key: value` and leaves YAML **list items** (`- value`) fully visible — and argv tokens have no `:` at all. The leak then happens inside the very dump you produced to prove the credential is being handled properly.

- Print **keys, line numbers and counts**, not values. The strongest mask is the value never entering the string.
- If a value must be seen to be understood, mask **per shape**: `key: value`, `- value`, and bare argv tokens need three different rules.
- Treat every dump of `config.yaml` or of a process list as a potential credential-leak incident *regardless of how well the mask worked last time*, and say so when it does leak — the alternative is a report that quietly teaches the next session the wrong mask.
