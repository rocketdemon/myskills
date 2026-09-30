# Config-migration rewrite semantics — it decides when a restore command actually takes effect

## One-line rule

In a migration, `_rewrite_stale_default(section, key, old=<old default>, new=<new default>)` fires when
**`current value == old default`**. Therefore:

| The final value you want | When writing it takes effect |
|---|---|
| **!= old default** (e.g. write 4 while the old default is 3) | **Writing it before the upgrade works** — the migration predicate does not match, so it is skipped ⇒ **it survives the upgrade** |
| **== old default** (e.g. write 30 while the old default is 30) | **Writing it before the upgrade does nothing** — the migration rewrites it anyway ⇒ **you can only write it after the upgrade** |

**Why this rule matters**: when planning an upgrade, every key that a migration touches must first be
sorted into these two classes, and only then assigned as a "pre-upgrade action" or a "post-upgrade
action". Scheduling a "== old default" key before the upgrade is wasted work — and **that mistake is
invisible afterwards**: the command succeeds and the value you read back really is the one you wanted,
it was just rewritten again during the upgrade.

**Generalizing to predicate matching**: not every migration uses equality matching. Some use
`functools.partial(_rewrite_key, ..., match=lambda cur: ...)`
(measured example: `agent.verify_on_stop` uses `match=lambda cur: cur is None or _lower_is("auto")(cur)`).
There the trigger condition is a **predicate**, so before pre-setting a value you must read that
predicate; you cannot assume "anything different from the old default is safe".

## Measured (execution-driven, not inferred from reading code)

With an isolated `APP_HOME` (a copy of the real config) plus the **target-version code**, actually run
`run_migrations(34, ...)` for three cases:

| case | preset | after migration |
|---|---|---|
| `all10` | nothing preset | all 6 keys **rewritten** (including 3→10, 30→14, 90→30, all→concise) |
| `real` | `max_concurrent_children=4`, curator left at 30/90 | **4 → 4, not rewritten** ✅; 30→14 and 90→30 still rewritten |
| `explicit_nondefault` | 4 / 31 / 91 | **all three preserved** ✅ |

⇒ The predicate really is "rewrite only if it equals the old default".

**Reusable probe**: `scripts/probe-config-migration-preset-survival.py` — a self-contained single file;
it is **read-only** with respect to the production config (every case is copied into a brand-new temp
directory; the script deletes nothing);
`--preset KEY=VALUE` is repeatable, and it automatically appends a control case with nothing preset.

```
python3 scripts/probe-config-migration-preset-survival.py \
    --config ~/.myapp/config.yaml --code <target_worktree> \
    --preset delegation.max_concurrent_children=4
python3 scripts/probe-config-migration-preset-survival.py \
    --config ~/.myapp/config.yaml --code <target_worktree> \
    --preset delegation.max_concurrent_children=4 \
    --preset curator.stale_after_days=31 --preset curator.archive_after_days=91
```

How to read it: **the preset stays unchanged in the AFTER run while the same key is rewritten in the
control case** ⇒ this proves "the preset survives the migration".
At the end the script echoes the sha256/mtime of the production config, as proof that the probe never
touched it.

**Why the migration comment saying `an explicit user value is preserved` is only half true**: it only
guarantees that a value **different from the old default** is preserved. "**Explicitly set to the old
default**" and "**never set at all**" are indistinguishable in the implementation, and both get
rewritten. ⇒ Seeing that sentence **must not** lead you to conclude "the value I set is safe".

## Two supporting facts

1. **`run_migrations()` does not write the version stamp** (measured three times; after it, the config
   still shows `_config_version: 34`); `migrate_config()` is what stamps it.
   ⇒ When running the probe, "the version number did not change" **does not mean the migration did not
   run** — you must look at the key-level diff.
2. **A migration may only delete a key and not write a replacement**: the migration for
   `model_catalog.ttl_hours` is
   `_rewrite_stale_default(new=None, extra_guard=lambda raw: "ttl_minutes" not in raw)`
   ⇒ measured: in the AFTER run `ttl_minutes` is still `<missing>` (it does not exist), and the value
   comes from the **code default**.
   Consequence: after the upgrade the key is absent from the config; to change it in the future you must
   **write it explicitly under the new key name** — using the old key name will fail.

## "Is this value something I set, or was it the default all along?" — mandatory answer when planning the restore list

Method: cross-check the value in config.yaml against **both** places at once —
① `DEFAULT_CONFIG` (in `myapp.config`); ② **the fallback default in the code**.

Looking at ① alone misses things: some keys are not in `DEFAULT_CONFIG`, but the code supplies a default
when it reads them (measured example: `gateway/run.py` has `mode = (mode or "all")`), so `all` appearing
in the config still only means "never touched".

In one measured run all 6 keys up for restoration **equaled the current-version defaults** ⇒ there is no
evidence the user ever deliberately chose any of them.

**Communication implications (the user will push back on this)**:
- Do not describe "changing it back" as "restoring your preference" — that is **inventing user intent**.
  The accurate wording is "freezing the behavior on the old default you are used to".
- Nor should you present the upstream's default change as unjustified — the migration comment usually
  states why it changed
  (measured example: `subagent iteration cap 50 → 250 (50 truncated substantial delegated work)`); that
  is the decision evidence.
- Distinguish an "**upper bound / threshold**" from a "**target value**": raising a cap **does not by
  itself incur cost**; you are billed only for real consumption. Early on, describing "iteration cap ×5"
  as "tokens you can burn ×5" was wrong — the user decides based on cost consequences, so this
  distinction must be stated precisely.

## The four things to cover per key (the user asks "what exactly does it do", one by one)

1. **What it controls** — cite the code location / the official docs verbatim, not an interpretation
   derived from the parameter name
   (e.g. the legal values and semantics of `background_process_notifications` must be read from
   `cli-config.yaml.example` and from the reader code; the target version may **add values and change
   defaults** that the old docs do not contain).
2. What the migration changes it to + **the reason upstream gives** (quote the migration comment
   verbatim).
3. **The practical impact on me** — grounded in a concrete scenario (which platform, when I would hit
   it), and distinguishing cap vs target as in the previous section.
4. Whether the current value is a **user choice** or a **default** (use the dual-place cross-check
   above).

## Execution-timing checklist template

After planning is done, split the restore list explicitly into two lists; do not mix them:

- **Runnable before the upgrade** (value != old default; the migration skips it): ... (after running it,
  **replay the target-version migration engine on a copy of the real config** to confirm it was not
  rewritten)
- **Must be run after the upgrade** (value == old default; the migration will rewrite it): ...

```bash
# right after running the "pre-upgrade" half, re-verify that it really survived the migration
python3 scripts/probe-config-migration-preset-survival.py \
    --config ~/.myapp/config.yaml --code <target_worktree> \
    --watch delegation.max_concurrent_children,curator.stale_after_days,curator.archive_after_days
```

---

## Sibling pitfall: getting "when" right is not enough — get "which layer" right too

The predicate above settles **timing** (before/after the upgrade). A separate problem is **precedence** —
the value is written and the timing is right, yet it can still be shadowed by a higher-precedence layer
and never take effect.

**Before changing any config, read the resolution order out loud**:

```bash
grep -A25 'def resolve_display_setting' <repo>/gateway/display_config.py   # substitute the matching resolver
```

Measured order (`gateway/display_config.py::resolve_display_setting`):

```
1. display.platforms.<platform>.<key>   ← explicit platform override (returns on hit)
2. display.<key>                        ← ★ global explicit setting (returns on hit)
3. _PLATFORM_DEFAULTS[<platform>]       ← built-in per-platform preset
4. _GLOBAL_DEFAULTS[<key>]              ← built-in global default
```

**The counter-intuitive point**: step 2 comes **before the platform presets** ⇒ **a "global explicit
setting" overrides the "built-in platform preset"**. A quiet preset the platform author carefully
configured for a given platform (e.g. `"weixin": _TIER_LOW`, turning off `tool_progress` /
`interim_assistant_messages` for a platform with no editing capability) is completely nullified by a
single global `display.tool_progress: 'all'` in the config. Measured consequence: that platform gets
flooded with interim messages on long turns, hits delivery rate limiting, and **ends up dropping the
delivery of the final reply** (see your notes on silent-delivery-failure monitoring, §6-2).

⇒ Two rules of discipline:

1. **Do not guess precedence from "the more specific one wins"** — read the resolver's order; it is a
   code fact.
2. To change one platform's behavior without touching the others, **use layer 1 (the explicit platform
   override)**, because it comes first and does not affect the CLI or the other platforms; changing it
   via the global key changes every platform at once.

These three concerns are mutually independent; walk through them one by one when planning:
**timing (before/after the upgrade) × layer (platform/global) × how it takes effect (re-read every
turn, or snapshotted at construction time — see the same-named section in SKILL.md)**.
