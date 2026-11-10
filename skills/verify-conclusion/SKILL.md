---
name: verify-conclusion
description: Use when the agent has just produced a diagnostic conclusion, research finding, or any inference from collected evidence. Systematically stress-tests the reasoning chain and re-derives from scratch to catch premature conclusions, unverified assumptions, and causal fallacies.
version: 1.2.1
author: "rocketdemon"
license: MIT
trigger: Fires immediately after producing a diagnostic conclusion, research finding, or any inference from collected evidence. Also fires when the user says `确认吗`, `再验一下`, `复查`, `肯定吗`, `你验证了吗`.
metadata:
  hermes:
    tags: [verification, reasoning, debugging, quality]
    related_skills: [systematic-debugging]
---

# Verify Conclusion

## Overview

LLM agents are prone to declaring conclusions from incomplete evidence — confusing correlation with causation, stopping at the first plausible explanation, or asserting claims that can't be verified. This skill forces a structured second pass: attack the reasoning chain, then blind re-derive. If the two passes agree, the conclusion survives scrutiny. If they don't, iterate until convergence or escalate.

## Scope

- **No runtime dependency.** Markdown only: no scripts, no database, no network. It works the
  same in any agent harness that can read a skill file.
- **Examples are real incidents from one self-hosted agent host (2026).** Every count, threshold
  and window in them is a *historical measurement*, not a recommended setting — re-measure on
  your own system before reusing a number.
- **Two harness-specific spots, both flagged in place:** the transcript-search tool named in
  trap ⑤, and the byte-size attribution recipe in trap ⑰ (a companion write-up that is *not*
  bundled). Everything else is harness-agnostic.
- **Trigger phrases are kept verbatim in Chinese** (`确认吗`, `再验一下`, …) because they are
  what the user actually types; translating them would break the trigger.
- **Read-only unless a repair is explicitly approved.** The skill itself never executes
  anything. When a conclusion implies a repair, only state-*preserving* probes are allowed;
  anything that changes state (delete, service reload/restart, `VACUUM`, config change) needs
  the user's explicit approval first. Trap ⑨ is constrained the same way.

## When to Use

**Trigger immediately after** the agent produces any of:
- A diagnostic conclusion ("X caused Y", "root cause is Z")
- A research finding or synthesis
- An inference from collected clues/evidence
- Any claim marked as `关键结论` (key conclusion) or `根因` (root cause)
- **A remediation / handling plan** ("do X", "don't do Y because it would break Z", "no action needed") — a plan is a bundle of causal claims and negative claims, and it gets the same two passes. A plan that was proposed but not yet executed is exactly when verification is cheapest.

**Also trigger when user says:**
- `确认吗` / `肯定吗` / `再验一下`
- `你确定？` / `复查一遍`
- `用SKILL检查` / `用skill验证` / `用验证结论的skill`
- Any expression of doubt about a conclusion

**Do NOT trigger on:**
- Routine status reports (infrastructure checks, health pings)
- Simple factual lookups with no inference
- Steps within an already-verified chain

## Steps

### Step 1: Stress-test the reasoning chain

For every link in the reasoning chain, classify it:

| Type | Definition | Action |
|------|-----------|--------|
| **Fact** | Has direct evidence (log line, file content, timestamp, exit code) | Verify the evidence source still exists and is correctly cited |
| **Assumption** | Reasonable but unverified (default behavior, common pattern) | Flag explicitly, assess risk if wrong |
| **Inference** | Derived from facts (X happened, therefore Y) | Check for alternative explanations |

Then ask:

1. **Causality check:** Did X happen before Y, and is there a mechanism connecting them? Correlation is not causation.
2. **Upstream check:** For every cause identified, ask: what caused THAT? Stop only when reaching a system boundary or already-known factor.
3. **Verifiability check:** For every diagnostic claim, ask: "What command would prove this wrong?" If no such command exists, the claim is unfalsifiable — flag as weak.
4. **Completeness check:** Were any clues seen but not incorporated? Were any investigative steps skipped?
5. **Fix feasibility check:** Does the diagnosis imply a repair action (ALTER TABLE, config change, etc.)? If yes, run the most lightweight validation that confirms the fix can be applied before declaring the diagnosis complete. **The probe must not change state.** If the only available probe would itself perform the
repair (a real `ALTER TABLE`, a service restart, a `VACUUM`, a delete), stop and ask for approval first — a feasibility check that performs the repair is no longer a check. A diagnosis whose repair path is blocked is an incomplete diagnosis — flag it and identify the blocker. Examples: before declaring "add TTL" as the fix, run ALTER TABLE with a low-timeout dry-run; before declaring "restart service X" as the fix, check that the service can respond to SIGTERM. Do NOT defer this check to the implementation phase — a fix that can't be applied invalidates or constrains the diagnosis.

Output: either "No weaknesses found" with a specific defense of each link, or a numbered list of weaknesses.

### Step 2: Blind re-derive

Start fresh. Do NOT reference your first-pass conclusion. Collect evidence from scratch, form hypotheses, test each, and arrive at a conclusion independently.

**Critical — methodological independence:** Step 2 must use a **different reasoning strategy** than Step 1. The goal is not to use different tools — it's to approach the problem from a different cognitive angle, so the same blind spot doesn't trap both passes.

**Reasoning-strategy cross-check: what Step 1 used → what Step 2 must switch to:**

| Step 1 reasoning strategy | Step 2 must switch to | Why it differs |
|----------------|----------------|-----------|
| Symptom → cause (bottom-up) | Hypothesis → elimination (top-down) | Bottom-up tends to chase the first clue; top-down forces you to enumerate every possibility first, then eliminate them one by one |
| Inside-the-component view of A | Boundary/upstream view via B | Staring inside one component → cross-component dependencies get ignored |
| Log-driven (look for errors) | State-driven (read current values) | Logs only record what happened; state tells you what actually is |
| Timeline reconstruction (order of events) | Dependency-chain reconstruction (A depends on B) | Temporal correlation ≠ causation; the dependency chain is closer to real causation |
| Single-hypothesis verification | Competing hypotheses | Once locked onto one hypothesis you tend to seek only supporting evidence and ignore counter-evidence |
| Before/after comparison of a time series | Placebo / negative-control cut points | A before/after split gives the suspected change all the credit; fake cut points show whether the series would have moved anyway |

**Decision rule:** it is not about whether the tools sit at the same layer — it is about whether the **reasoning direction** changed. Different tool, same reasoning direction → false independence.

**Typical cases of false independence:**
- Step 1 used `journalctl` to read the Gateway log (bottom-up) → Step 2 used `tail logfile` to read the Gateway log (still bottom-up) → false independence: the tool changed, the thinking did not
- Step 1 used `docker exec` to inspect ClickHouse internals (inside-the-component view) → Step 2 used `curl` against the ClickHouse HTTP endpoint (still inside-the-component view) → false independence

The two passes agree → **Conclusion confirmed** ✅. Stop.

### Step 3: Re-derive again (up to 3 attempts)

If pass 2 disagrees with pass 1, record the specific difference. Then re-derive a third time, blind to both previous attempts.

If passes 2 and 3 agree with each other (but not pass 1), accept the majority result. ✅

If all three disagree with each other:
- Document: what was attempted, what each pass found, why they differ
- State: **"The conclusion is not self-consistent — human intervention required."**
- Stop.

### Completion criteria

- Every link in the reasoning chain classified as fact/assumption/inference with source
- At least two independent derivations performed
- Agreement reached OR escalation with clear documentation of divergence

## Common Pitfalls

Derived from real agent errors in this environment:

### ① Temporal correlation mistaken for causation

**Pattern:** "After upgrading X, Y broke" → declared as root cause without testing rollback or finding mechanism.

**Fix:** Before declaring causation, isolate: can you reproduce the fault with X at the old version? If not, the statement must be labeled "unverified inference (temporal correlation only)".

### ② Stopping at the first plausible explanation

**Pattern:** Found "Gateway write-lock timeout" → declared root cause. The real cause (journal corruption → I/O storm) was two layers upstream and never investigated.

**Fix:** After every diagnostic conclusion, ask: "What caused this condition?" Repeat until hitting system boundary or known factor.

### ③ Unverifiable diagnostic assertions

**Pattern:** "System is healthy," "Problem resolved," "Everything normal" with no specific evidence.

**Fix:** Every diagnostic claim must be answerable with "What command verifies this?" If no single command can verify it, either break it into verifiable sub-claims or label it "speculation".

**Same burden for soothing negative guarantees.** "These few calls won't make anything worse", "this can't touch production", "nothing else reads that file" are claims, not courtesies. The *absence* of a side effect needs the same evidence as its presence: name the mechanism that would have produced it (rate accounting, cooldown state, lock ownership, reader list) or say "未验证". A reassurance the user then acts on converts your guess into their decision — and the later contradicting observation reads as a broken promise instead of a normal unknown, which costs more trust than admitting the gap up front.

### ④ Reflexive attribution to recent changes

**Pattern:** Any recent config change, upgrade, or modification is reflexively blamed for any new symptom. The upgrade WAS the most recent change, but that doesn't make it the cause.

**Fix:** List at least one alternative explanation that does NOT involve the recent change. Attempt to falsify it.

### ⑤ Intermediate state reported as final conclusion

**Pattern:** A transcript-search DISCOVERY result shows "X is pending" in the middle of a session, but the same session's bookend_end resolved it. Agent reports the pending state without checking the end.

**Fix:** After any lookup (especially a search that returns partial, paginated, or time-sliced results), always check whether the same source later updated or invalidated the finding.

### ⑥ Apologize first, investigate later

**Pattern:** User challenges agent → agent says "you're right" → then investigates → discovers user was wrong too.

**Fix:** When challenged, locate the original evidence first. Respond with the evidence. Decide who was right based on the evidence, not social dynamics.

### ⑦ Agent forgets to auto-trigger verification

**Pattern:** Agent produces diagnostic conclusions → moves on to the next task without loading this skill. Only applies it when user explicitly asks `你验证了吗？` or `这个结论你用verify-conclusion了吗？`.

**Fix:** After producing ANY conclusion that matches the trigger criteria, immediately ask: "Did I just produce a diagnostic conclusion?" If yes, this skill MUST be loaded before moving on. User prompts like "did you verify?" ARE legitimate triggers — do not treat them as optional.

### ⑧ Testing with fake credentials produces false results

**Pattern:** When testing an API-dependent service, using a fake key (e.g. "sk-test") can cause completely different behavior — timeouts, slow startup, different error paths — compared to the real key. Agent concludes "service X is slow" when the real issue is the fake key triggering a different code path.

**Fix:** Always test with the REAL credentials when available. If using fake credentials is unavoidable, explicitly state "this behavior may differ with real credentials" and do not draw performance/timing conclusions from fake-key tests.

### ⑨ Diagnosis complete but fix blocked — verification stopped too early

**Pattern:** Agent validates every link in the causal chain → declares diagnosis confirmed → user asks to apply fix → fix fails immediately because the repair path was never tested (e.g., ALTER TABLE hits data corruption, restart doesn't actually clear the problem).

**Fix:** After Step 2 confirms the causal chain, Step 1's completeness check MUST include a lightweight fix feasibility test before the diagnosis is considered "complete." A diagnosis whose repair is blocked is a partial diagnosis.

**Real case (2026-07-23):** Agent diagnosed ClickHouse system table TTL missing → verified all causal links → user approved fix → ALTER TABLE failed with CHECKSUM_DOESNT_MATCH (data corruption from HDD + unclean shutdowns). The diagnosis was logically sound but practically incomplete because the repair path wasn't validated.

### ⑩ User asks to run the SKILL check = execute the full Step 1 + Step 2

**Pattern:** The user says "run the SKILL check once" (`用SKILL检查一次`) → the agent loads the skill and does a quick confirmation (one paragraph: "verified, both passes agree") → the user immediately says "run the skill check **one more time**" (`用skill检查一遍`) — meaning the first attempt was not enough.

**Root cause:** The agent reads "check with the SKILL" as "cite the skill for a quick confirmation", while the user expects the **full Step 1 + Step 2 — the item-by-item classification table and the blind re-derivation process**. Users can tell "verbal confirmation" apart from "actually running the process".

**Fix:** When the user says "`用SKILL检查`", the output MUST be complete:
1. Step 1 — item-by-item classification (Fact/Assumption/Inference) + physical evidence + falsifiability test
2. Step 2 — blind collection of evidence from scratch (not referencing the first pass) → independent derivation → comparison
3. An explicit statement that "both passes agree ✅" or the points of divergence

**Fix:** In a diagnostic context, hearing "`用SKILL检查`" / "`用skill验证`" means: load verify-conclusion first and execute the complete Step 1 + Step 2. Only afterwards consider whether a domain skill is needed to verify the cited facts.

### ⑪ Output from a restricted tool treated as fact

**Pattern:** The agent verifies whether a file exists using `sudo cat /etc/sudoers.d/X` → sudo refuses because the NOPASSWD scope does not cover `cat` → the `|| echo "FILE NOT FOUND"` fallback fires → the agent asserts "the file does not exist". The file **existed all along**; `sudo` simply lacked permission to run `cat`.

**Root cause:** A false negative caused by a tool-permission limit is taken as truth. `sudo` refusing ≠ the file does not exist — it means "you have no NOPASSWD authorization for this command". Every subsequent step of the diagnosis then rests on a false fact.

**Fix:** Any file-existence check that relies on `sudo` must first be redone with a tool that needs no sudo — `ls -la` or `stat`. File existence (an inode property) and file readability (a permission property) are **two independent propositions**; conflating them produces catastrophic misjudgments.

**Real case (2026-07-27):** Agent asserted `/etc/sudoers.d/<name>` had been deleted → inferred "the Phase 4 fix never ran" → after the user challenged it, re-inspection showed `ls -la /etc/sudoers.d/` listing the file all along, and `sudo -n journalctl --flush` returning 0. The root cause was not a missing file but the permission-limited `sudo cat`.

### ⑫ Tool output treated as a logical assertion (the `|| echo` anti-pattern)

**Pattern:** The agent checks a resource state with `cmd 2>/dev/null || echo "X NOT FOUND"` → the tool itself fails for any of many reasons (permissions, timeout, env) → `|| echo` fires → the agent asserts "X does not exist / failed". In reality `cmd` can fail for infinitely many reasons — insufficient permission, timeout, command not found, missing TTY, different PATH — and those are **completely unrelated** propositions to "X does not exist".

**Root cause:** `||` compresses two independent signals — "did the tool execute successfully" and "is the resource in a given state" — into a single boolean. That is very natural under shell-writing habits, and catastrophic for scenarios that require precise diagnosis.

**Fix:** Never use the `|| echo "NOT FOUND"` / `|| echo "FAILED"` anti-pattern in any diagnostic check. A diagnostic check must distinguish explicitly:
1. Did the tool run successfully (exit code)
2. Is the resource in the claimed state (content / return value)

Print the two separately; do not merge them with `||`. Typical wrong form vs correct form:

```
❌ sudo cat /f || echo "FILE NOT FOUND"
❌ curl -s X || echo "API DOWN"
✅ ls -la /f; echo "exit=$?"          ← first confirm the tool itself ran
✅ curl -s -w '\nHTTP:%{http_code}' X ← two lines: body + exit, independent
```

**Related trap — false independence in Step 2:** when Step 2 uses the same restricted tool as Step 1 (e.g. `sudo test -f` and `sudo cat` share the same NOPASSWD whitelist limit), both "independent" derivations in fact share one hidden premise — a single cause breaks both at once. True independence requires Step 2 to use a **different access path** (e.g. `ls -la` instead of `sudo cat`).

**Same family — the silent-failure aggregator.** Any loop that sums per-object reads with errors discarded undercounts while looking complete:
```
❌ for p in /proc/[0-9]*; do s=$(awk '/^Pss:/{print $2}' $p/smaps_rollup 2>/dev/null); [ -n "$s" ] && tot=$((tot+s)); done
tot=${#ok}  →  prints a total; 74 of 84 reads failed silently → total is 44% of truth
✅ count successes and failures explicitly, print both, and refuse to present the total when failures > 0
```
Rules that follow:
1. **A permission pre-check is not a readability check.** `[ -r /proc/<pid>/smaps_rollup ]` once reported "all readable" while most reads in fact failed — on `/proc`, ownership and access(2) semantics do not settle it. Only an actual read that returns a value counts.
2. **When the probe's own number disagrees with an independent total, the probe is on trial, not the total.** Here `/proc/meminfo` `AnonPages` (2.2 GB) vs the summed PSS (1.0 GB) exposed the bug; chasing the discrepancy found it. Never reconcile a gap by explaining it away.
3. **Cross-basis summing is not reconciliation.** Two aggregates measured on different definitions (container "memory usage" that includes reclaimable file cache vs host process RSS; per-process RSS that double-counts shared pages) can land within a few percent of a system total by coincidence. Agreement across mixed bases is not evidence — convert everything to ONE basis first (e.g. anon/`AnonPages`), then add.
4. **A negative claim about a system knob needs its own measurement.** "`swapoff` would OOM because swap is 80% full" is a claim; compute the actual comparison (`MemAvailable` vs swap in use) and grep the OOM history before repeating it.
5. **Printing the caveat is not compliance.** Writing "RSS double-counts shared pages, relative ranking only" and then summing RSS + swap into a "total" one line later is the standard failure shape: the caveat was emitted, so the number feels licensed, and the reader's eye lands on the total. A caveat that forbids an operation must be followed by *not doing it* — either drop the total or convert every term to one basis first. Same for any "⚠️ not exact" annotation attached to a headline figure.

### ⑬ Declaring "root cause found" on the first bug

**Pattern:** The agent finds A is problematic → asserts "the root cause is A" → the user asks about a specific scenario → the agent discovers A is in fact not the problem → then finds B is the real root cause. Between finding A and finding B, the agent had already declared "root cause found" once.

**Root cause:** Root cause is an elimination process, not a discovery process. A single anomaly ≠ root cause. Only after three steps — falsification test, elimination of alternative explanations, causal-chain verification — may it be declared.

**Fix:** A diagnostic conclusion must be explicitly labeled "candidate root cause #N" or "not yet eliminated" until the verification workflow has run to completion. Declaring "root cause found" is forbidden before every one of the following holds:
- [ ] Alternative explanations have been isolated and eliminated
- [ ] Every link in the causal chain has physical evidence
- [ ] The repair path has been verified to be feasible

**Real case (2026-07-27):** Agent found the sudoers file "missing" (actually a permission false negative) → declared "root cause found" → the user asked who deleted it → re-inspection showed the file had been there all along → the real root cause was `Restart=always` bypassing stop.

### ⑭ Still attack after two passes agree — user-driven deep review

**Pattern:** Two independent derivations agree → the agent declares "confirmed" → the user says "attack this solution" → the agent discovers a deeper hard limit.

**Root cause:** Two passes may converge on an intermediate-layer conclusion — correct but incomplete. Case from this session: the journal dirty shutdown; round 1 → `After=dbus`, round 2 → `Restart=on-failure`, round 3 agreed. After the user asked to attack the solution, the real bottleneck turned out to be the **WSL hard-coded 10s shutdown timeout** (journald flushing on an HDD can exceed 10s → exit≠0 → `on-failure` still triggers a restart).

**Fix:** Once two passes agree, automatically deep-attack before declaring "confirmed":
1. Can every premise of the fix be violated?
2. Is there a hard limit (hard-coded timeout, kernel behavior) that defeats the plan?
3. If a premise is violated, what is the consequence and what is the fallback?

### ⑮ Empty-string exception trap (`str(exc) == ""`)

**Pattern:** Exception is logged but `str(exc)` returns `""` — "keepalive failed: " (nothing after colon). Agent assumes benign and stops.

**Root cause:** `CancelledError()`, `ConnectionResetError("")`, and un-unwrapped `BaseExceptionGroup` all produce empty `str()`. The `_is_method_not_found_error` pattern checks `if not msg: return False` → empty exception treated as real failure → infinite reconnect loop.

**Real case (2026-07-28):** Both journal AND MCP investigations misled:
- MCP: `keepalive failed, triggering reconnect: ` — empty. Caused 6,212 process spawns in 63h. Root cause: `_is_method_not_found_error` returned False on empty `CancelledError()`
- Journal: `systemctl: D-Bus connection terminated` — an empty `ConnectionResetError` hid a Hyper-V IPC break

**Fix during diagnosis:**
1. If `str(exc) == ""`, check `type(exc).__name__` and `repr(exc)` — not just the string
2. For `BaseExceptionGroup`/`TaskGroup` errors, unwrap with `_unwrap_exception_group`
3. In verify-conclusion, classify empty-exception links as **Assumptions requiring type-level investigation**, not Facts
4. When proposing fixes, test: does the fix survive the empty-exception case?

### ⑯ Empty result ≠ unsupported parameter — trace the data the special case depends on

**Pattern:** A query returns `[]` with an optional parameter and returns rows without it → the agent declares "this version doesn't support that filter / silently matches nothing" and stops. The parameter is in fact supported, and is special-cased: it is popped out of the ordinary filter dict before validation (so it never raises the "column not allowed" error that a genuinely unknown key raises), then applied as a **data-dependent predicate** — membership windows, soft-delete flags, time bounds. The result set is empty because the boundary values in that table exclude every row, not because the parameter was ignored.

**Diagnosis rule — three checks in this order:**
1. **Grep the server source for the key**, not just the API response. Special-cased keys are handled separately from the validated filter whitelist, so "no error" tells you nothing about whether the key was understood.
2. **Read the predicate.** Whatever table the special case joins against, query it and compare its boundary columns against the row timestamps in the result set (joined-at / left-at, created-at windows, deleted-at). The empty result is explained when the boundary sits *after* the rows you expect.
3. **Find a positive case through the same parameter**, and it proves the parameter works: pick content that falls inside the window and re-run. A successful hit through the same filter kills the "unsupported parameter" hypothesis outright.

**Severity calibration:** without that positive case, the honest claim is "returns nothing for X"; "the feature is dead for everyone" is only allowed after a query inside the window also comes back empty.

**Upstream rule:** if the boundary value is suspiciously recent (equal to a process restart, a login, or an upgrade moment), the boundary is being **written** by something, not merely read — follow it to the writer (`grep` the upsert / `on_conflict` clause). A predicate whose boundary is reset on every restart silently shrinks the searchable set each time, which is a different (and worse) defect than a broken filter.

### ⑰ Plan verification: three shortcuts that fabricate a mechanism

A remediation plan ("clean up X", "don't touch Y", "no action needed") is where these three bite hardest.

**a) Never derive a mechanism by subtracting counts measured over different scopes.**
The classic: "row count rose by N, but only M rows carry a timestamp inside the reporting window, therefore N−M rows are re-inserted history." That subtraction is invalid whenever (i) the window's upper bound is earlier than *now* — everything written after the last measurement falls outside it; (ii) timestamps are not monotonic (backfills, imports, queued writes). Both are common.
**Fix:** use **insertion order** (the autoincrement PK / rowid that already existed in the older snapshot) or **two snapshots of the same table**, then test the claimed mechanism directly — does any new row match an existing row on the natural key? Zero matches kills "re-insert" outright.

**b) Separate stock from flow: measure the same quantity in an older snapshot and now.**
An anomaly is either pre-existing stock (already there before the window) or new flow (grew during it). One snapshot gives only the current level. Subtract the historical snapshot's measure and the two separate. Before blaming X for growth: if X already held ~97% of its current value in the last backup, X is not what grew this window.

**c) Negative claims carry the same evidence burden as positive ones.**
"Why we must not do Y" is a claim; unverified it hardens into a permanent false constraint. Two prohibitions that each collapsed under a single query: "deleting rows would desync the search index" (the table had insert/delete/update triggers — it would have stayed in sync); "VACUUM needs an equal amount of free space" (hundreds of GB free; the real cost was an exclusive lock, not space). Verify the mechanism behind a prohibition exactly like a proposed fix; if it fails, keep the recommendation only if a *different* verified reason supports it — and say which reason survived.

**Domain note:** for byte/disk-size anomalies the concrete attribution recipe (per-object deltas, index amplification factor, reconciliation across independent measures) is not bundled here — it is a companion write-up, not a requirement of this skill. The mechanism above stands on its own; treat the recipe as an optional next step.

### ⑱ A skipped assertion reads exactly like a passing one — prove the exercised branch fired

**Pattern:** the verification harness reports `24 checks, 0 failed` while the assertions that matter never executed. Two shapes, both silent:
- **Nested under a guard on the artifact.** `if output.strip(): check(...)` — the detector short-circuited (e.g. "insufficient history, skip detection") and produced no output, so the output-dependent checks simply did not run. The suite is green and the feature is untested.
- **Fixture violates a precondition of the code path.** A rule like `if std > 0 and delta > mean + k·sigma` can never fire when the fixture's deltas are perfectly constant (std = 0), and `if len(baseline) >= MIN_DAYS + 1` can never fire on a fresh fixture. Both produce empty output, which the harness counts as nothing happening.

**Fix — in this order:**
1. Assert **reachability first, unconditionally**: a top-level check that the artifact is non-empty / the branch was entered, placed before any content assertion and never nested under `if output:`. Its failure message must carry the raw output and stderr.
2. Make the fixture satisfy every precondition of the path — enough history rows, and **variation** where the rule divides by or thresholds on a variance term. A fixture with constant increments disables every sigma-style rule.
3. Distinguish "no signal because clean" from "no signal because the path did not run" **in the product itself**: have the code emit which rule fired (threshold value, rule name). Then the absence of a rule label is itself evidence the branch was skipped.

**Unit-label corollary:** a derived metric labelled `KB` while computed in bytes passes every "contains the word KB" style check and is wrong by 1024×. Assert the **magnitude**, not the presence of the unit string (`float(seg) < 100`), and re-read one real run's output rather than trusting the test to have covered it.

**Related trap:** a whole-suite green from `run-all-criterion-tests.py` only proves each script exited 0. If a script's own assertions are conditional, the suite inherits the silence.

### ⑲ Before/after comparison without a control group proves nothing about the change

Any claim shaped "metric M moved after change X, therefore X worked" is an **inference with a confounder**, not a fact.
Time-series averages drift on their own, so slicing at almost any date produces a visible "shift".

**Fix — run the specificity test before printing any verdict:**
1. Take several **fake cut dates** besides the real one, spanning both sides of it.
2. Compute the same statistic (e.g. mean-before ÷ mean-after) at every cut.
3. If fake cuts yield an **equal or larger** shift — especially cuts that fall **before** the change, where it cannot possibly be the cause — the shift is not specific ⇒ the only honest verdict is **cannot determine**. Not "worked", and not "didn't work".
4. **Use equal-length windows on both sides of every cut, not whole-period means.** On a drifting series a whole-period before/after ratio lands below 1 at *every* cut, so every cut "looks like an improvement" and none is specific. Take the same number of data points immediately before and after each cut (e.g. 5 vs 5) and compare those. Then judge the **shape** at the real cut: a step/level break that never returns to the old level is the only shape that survives scrutiny; a slow monotone drift gives near-identical ratios at neighbouring fake cuts and settles nothing.
5. **Report one verdict per metric, never one verdict for the change.** A single change routinely yields mixed evidence: one metric bottoms out exactly at the real cut (suggestive) while another metric's minimum sits on a placebo cut (cannot determine). Collapsing them into one sentence either invents a success or destroys a real effect — print each metric's ratio, its verdict, and the confounder you could not exclude (e.g. a metric that also moves with session structure or traffic composition, not only with the change).

**"No effect" is a positive claim and needs positive evidence** (the distribution's ceiling/mode unchanged, the counter unmoved). It cannot be inferred from "the average did not drop" — an average can stay flat while a real effect exists, or drop for unrelated reasons.

**Companion trap — ratios whose numerator and denominator share a driver.** `total spend ÷ run count = cost per run` looks like a decomposition but both terms are functions of daily activity, so the ratio is near-constant by construction (observed: spend −15% and runs −15% simultaneously, giving 0.999, which "proved" that savings came from running less — a synthesised conclusion, not a measurement). Either find a denominator from an independent instrument, or report "both moved together, they cannot be separated".

**Same family — proving nothing changed requires checking the right object.** Before asserting "the count/size/total did not change", confirm the object you measured is the live one: leftover stores from before an upgrade keep old data and stop at the upgrade date, which reads exactly like "the writer stopped". Liveness = a 2xx write request in the access log + rows carrying the current period + only one instance of its kind. Two of three is a hypothesis, not a fact.

### ⑳ An alert describes the trigger condition, not the outcome

**Pattern:** a monitor fires `Job 'X' failed` / `worker exited with status 1` / `task crashed` → the agent adopts the alert's
framing and starts explaining *why it failed*. The system of record shows the same execution as `completed` and
delivered, with the full artifact produced. The alert described a **process event** (a supervisor watched its child
exit before the terminal record landed), not the task's result.

**Fix — in this order:**
1. Resolve the **outcome** from the authoritative ledger before writing any causal sentence: job store, execution
table, delivery table, and the produced artifact. Classify as `completed` / `failed` / `unknown`. **`unknown` is its
own state, not a synonym for `failed`.**
2. The alert line usually names only the detected event. The **real exception is chained into a different, longer
line emitted the same second** (`… paused with unverified admission: … : <root cause>` sits at the *tail*). Read that
line whole — `| cut`, `head -c`, or a viewer's column limit silently deletes the root cause and leaves you
explaining a wrapper message.
3. All-quantifier claims ("it fails every time", "it never succeeds") must be **counted in the ledger**, with the
retention window stated. These stores rotate (observed: only the last four rows survived), so "always" may rest on
a sample of one.
4. When alert and ledger contradict, the deliverable is **"this alert is a false positive — the run completed, here
is the artifact"**. Never manufacture a causal story for a failure that did not happen.
5. A **scheduler that keeps advancing `next_run_at` while `last_run_at` stays frozen** looks like a stuck job but is
not proof of one: dispatch attempts can miss their ack window and the job still runs minutes later. Decide from the
ledger + the produced artifact + the job's output directory, and re-read `last_run_at` before reporting "it hasn't
run". A watchdog line `worker did not acknowledge within Ns` coexisting with `status=completed` for the same window
is the same trap in a different coat.

**Related trap — evidence with a short half-life.** Worker stderr captures and reconcile/admission files are consumed
or unlinked once the task finishes; re-deriving later finds empty directories and a truthful "cannot determine".
Copy the on-disk evidence to a scratch directory during the *first* pass, before analysing it.

### ㉑ The observation point is not the object — a component's diagnostic string is not a verdict

**Pattern:** the agent measures a *neighbouring* object and calls it evidence.
- `/proc/<pid>/environ` is the **exec-time** environment snapshot. In-process `os.environ[...] = ...` never writes
  back to it, so "0 of N dotenv keys present in the gateway" says nothing about whether the file was loaded — the
  conclusion drawn from it ("writing to `.env` cannot work") was flatly wrong while the feature worked.
- A message produced by a **fallback branch** describes the branch, not the component that ran. A string like
  `no DISPLAY is set — X11 is not reachable` is chosen when discovery returned nothing; a different build whose
  spawn *failed* lands in the same branch, so the string is never proof of "the wrong binary is selected".

**Test:** ask "does this observation point move when the asserted object moves?" If the proxy can stay identical
across both possible states, it is not evidence. Then get an observation that *is* the object:
1. `grep` the source for the exact string and read the condition that selects it — the branch tells you what the
   message actually reports.
2. Exercise the claim in a **fresh process** rather than reasoning about a long-lived one (call the loader, then read
   the value; run the tool's own `doctor`/`status` subcommand).
3. Prefer the tool's declared self-report (`… doctor` printing the resolved path / active session) over any inference
   from side channel files.

**Corollary — the same trap in the negative direction:** "I checked the proxy and it looked consistent" is how a
wrong correction gets issued. Before retracting a working setup or contradicting your own earlier verification,
reproduce the mechanism at the real observation point; an over-correction is as costly as an over-claim.

**Corollary — "my own check came back empty" may just be the wrong field.** When a self-check reads empty or zero
off an artifact you just produced, settle the **field index** before concluding the producer emitted nothing.
Observed: a 7-column TSV verified with `awk -F'\t' '{print $9}'` returned empty → reported "the container column
is empty, the script is broken"; the data sat in `$7` and was complete. A check aimed one column off produces output
identical to a real defect, and because it is *your own* check you trust it faster than a third-party tool's.
Derive the column count from the format string itself (`awk -F'\t' '{print NF}'` on one real line), print it
beside the extracted value, and assert the expected count in the same check.

**Corollary — a probe must be able to observe the property you are asserting.** Reachability was tested by reading
an HTTP status code off ports 135/445, got `000`, and was reported as "unreachable" — those ports are not HTTP
services, so the probe could not have observed reachability in either direction. A TCP connect against the same
ports succeeded. Any probe whose output is invariant across both possible states of the claim is not evidence
(the same test as above, applied to the *measurement protocol* rather than the object). Also run a **known-positive
and a known-negative case side by side**: without a control, "no response" and "wrong protocol" are the same
observation.

### ㉒ The default preset is not the capability ceiling

**Pattern:** a tool or web UI opens on a default option (last 30 days, current period, first-level menu, default
filter). The result looks coarse — daily instead of hourly, summary instead of breakdown — so the agent declares
"this platform cannot produce X granularity" and writes it into a report. The capability was one non-default choice
away: a custom range whose start and end are the SAME day/hour yields the fine-grained rows, an "advanced/more"
branch holds the switch, the export dialog has its own granularity control.

**Fix — exercise the option surface before any "cannot" claim:**
1. Open the range/period/step selector and try the **extreme** admissible setting (single day, single hour, one
   row) rather than only the presets.
2. Open every `高级` / `更多` / `自定义` branch of the same control and of the export/save dialog.
3. Only if the finest admissible selection still returns coarse data may you state the ceiling — and say which
   option you tried, so the user can correct the path instead of just the conclusion.

This is a claim about the **option surface**, not about the data: it fails the "what command/click proves this
wrong?" test until you have actually selected the fine-grained option.

### ㉓ A known-broken instrument's silence is not evidence of inactivity

**Pattern:** telemetry for a period is missing → the agent reads "last record at T, nothing after T" as "nothing
happened after T" and reports the loss as ≈ 0. But the instrument had been down for the whole period, so its
silence carries **no information about activity**. Measured counter-example: the supposedly quiet window held a
full turn (5 assistant messages + 4 tool results) — real work, none of it recorded. The loss estimate was off by
an entire turn, and the error ran in the direction of understating.

**Root cause:** this is absence-of-evidence **inverted**. The familiar trap treats a missing observation as proof
of health; this one treats a known gap as proof of inactivity. Both use the instrument's output as if it tracked
the system, when the instrumentation is itself the thing that broke.

**Fix — in this order:**
1. Establish **when the instrument was writing at all** before interpreting any window. If it was down across the
   whole window, every claim about that window must come from a source that was up.
2. Size the gap from **measured activity in an independent ledger** (message / turn / row counts filtered by
   timestamp), never from the interval between consecutive records.
3. Express the loss in units that ledger can produce (turns, calls, rows). "≈ 0" is admissible only after that
   count comes back zero — not because the record stream was empty.

**Test:** "If activity had occurred here, would this instrument have recorded it?" When the answer is no, the
observation is not evidence in either direction.

### ㉔ Counting log lines that mention an object is not counting the event you claim

**Pattern:** `journalctl -u docker | grep -c '<container-name>'` returned 5 and 13 for two days and 1 for a third
→ reported as "the container **crash-restarted** 5 and 13 times" → built a causal story ("restart churn → memory/CPU
spike → plugin load timeout") on top of it. Every one of those lines was `sbJoin` (network endpoint rebinding); a
`grep -E 'restart|die|kill|health'` over the same window returned **zero** events. The whole explanation collapsed,
including the "why did today work" answer that was its only payload.

**Mechanism:** a container/process name appears in nearly every log category — networking, attach, exec, health,
signal. Substring-matching the name counts *mentions*, and mentions are dominated by whichever subsystem is noisiest,
not by the event under investigation.

**Fix — three checks, cheapest first:**
1. **Count the event verb or the structured field**, not the object name: `grep -E 'restart|die|kill|OOM'`, or parse
   `level=`/`msg=` fields. Never infer "restarted N times" from name matches.
2. **Sanity-check the implied rate.** A container restart count implies a restart interval; neighbouring timestamps a
   few seconds apart (observed: 14 s between "Next.js restarts") are physically impossible for that runtime. **An
   implausible derived rate is proof the event semantics are wrong**, not a surprising finding to report.
3. **Prefer the component's own counter** (`docker inspect -f '{{.RestartCount}}'`, a supervisor's restart tally, a
   status subcommand) over log archaeology. If the authoritative counter cannot reach back far enough, say the
   historical count is **unknown** rather than reconstructing it from text.

**Consequence rule:** when a causal story's *only* discriminating evidence is a count that later turns out to be a
mis-parse, the story does not degrade to "weaker" — it degrades to **cannot determine**, and any "and therefore
X" sentence built on it must be retracted explicitly.

### ㉕ "It self-healed" needs a re-measurement, not one later success

**Pattern:** `N` export failures clustered in one minute, then a successful write ~30 s later → reported as
"transient jitter, self-healed within 3 minutes, low impact". Re-reading the same counter later in the session showed
the count had risen again. The failure mode was **intermittent at low rate the whole time**; the interleaved successes
were never evidence that it had stopped.

**Mechanism:** a later success proves the code path *can* work. It says nothing about the failure rate, and
intermittent failures produce successes between them by construction — so "failure followed by success" is the normal
signature of a *still-active* low-rate fault, not of recovery.

**Fix:**
1. Report the shape as `N failures, first at A, last at B` plus the observation window — never the adjective
   "transient" on its own.
2. **Re-read the counter at the end of the session** before writing "recovered / self-healed / back to normal".
   A counter that grew across the investigation is a live defect regardless of how sparse it looks.
3. If you cannot re-read it, the honest verdict is **"intermittent; count as of T = N"** — "self-healed" is a claim
   you have not earned and it suppresses the only alarm the system was raising.

### ㉖ One measurement window is not a rate — define the window before characterising it

**Pattern:** a single 10-second sample showed a large swap-out spike → reported as "swap thrashing is continuous";
six further 10-second windows showed almost nothing → retracted to "periodic spikes"; re-reading the free-swap
counter hours later showed it had been falling steadily the whole time, which neither window had measured. Three
verdicts, each drawn from a window too short or too narrow to support it.

**Mechanism:** "steady", "periodic", "bursty", "high" are properties of a **rate**, and a rate needs a stated
denominator (window length) plus a baseline (what this metric does when nothing is wrong). A single sample supplies
neither, so whichever value it happened to land on silently becomes the characterisation — and a second sample that
disagrees just swaps one adjective for another.

**Fix:**
1. **State window and units before the adjective** (`N pages / 10 s`), take ≥3 equal-length consecutive windows, and
   report the spread (min–max or the per-window series) instead of one representative number.
2. **Prefer a monotonic counter over a windowed rate** when the question is "is this still happening": a free-space /
   total-events counter read at the start and again at the end settles it with no window-length argument at all.
3. **No baseline ⇒ no adjective.** "No historical baseline, so this level cannot be called abnormal" is a legitimate
   finding; "high" is not. When you install the sampling that would create a baseline, say that the first verdict
   is only available later.

### ㉗ A compound forecast is only as strong as its weakest factor — and every optimistic factor compounds

**Pattern:** a claim shaped `baseline × reduction-A × reduction-B ⇒ ¥Y saved per month`. The baseline came from the
**most active 5-day window** while being called "the daily average" (it was 1.8× the full-period mean), and the two
reduction percentages were never measured at all. Presented as a plan with a number attached; when re-checked, the
figure was 2–2.5× the real ceiling.

**Mechanism:** unlike pitfall ⑲ (one change, one before/after) this is a *product*, so verification must treat each
factor separately — and the failure is not symmetric. Baseline inflation and invented reductions are both biased in
the **favourable** direction (you propose a saving, so you reach for the flattering input), so they multiply instead
of cancelling. A product with one measured factor and two guessed ones is a *guess*, regardless of how many digits
it carries.

**Fix:**
1. **Decompose to the measured factors before printing any total.** For each factor write `measured / assumed`
   explicitly in the sentence that carries the number. If any factor is `assumed`, the total may not appear in a
   conclusion sentence — only as a labelled upper bound.
2. **Baseline window must match the claim's scope.** Reporting "per month" requires a full-period mean; a recent
   peak window, a best-campaign week, a post-change stretch are all **selection-biased baselines**. Then compare the
   *target* against the baseline: a target more generous than the current average means the claimed saving does not
   exist at that magnitude.
3. **Cross-check the top of the product with the bottom.** Compute the same forecast from the most pessimistic
   admissible factor values. If the plausible range spans an order of magnitude, the deliverable is the range and
   the dominant uncertainty — never the mid-point alone.
4. **Rank the levers after measuring composition, not before.** Where a total is the sum of parts with known shares,
   an improvement to a part can only move the total by `Δpart × share`. Without the shares you will order the plan
   by which lever *sounds* strongest (observed: the weakest lever, worth a 20% share, was listed first and the
   strongest, on a 42% share, third). Measure shares first; the ranking is then arithmetic rather than opinion.
5. **Apply the perturbation only to the terms it actually drives — write the driver map first.** A "−32%" applied
   uniformly across every term is a *hidden assumption* that all terms share one driver. Observed: request count
   was cut 32% and applied to both cached-input and uncached-input tokens, giving 19.8% saved; request count only
   drives the cached term (each request re-reads the context), while the uncached term is driven by *new content*
   and barely moves — the real figure was 6.4%, a 3× overestimate. Before propagating any change, list every term
   and answer "what physically produces this quantity?"; a term whose driver is not the thing you are changing
   must be held constant. Uniform perturbation is the default failure because it looks symmetric and therefore fair.

**Test:** "for each factor, which command produced this number?" Any factor with no answer is an assumption, and
it drags the whole product down to an assumption.

### ㉘ Proving a parameter took effect: compare identity, not a payload hash

**Pattern:** to test whether `&start=3` / `page=2` is honoured, hash the whole response and compare
hashes across variants. Three variants → three different hashes → "the parameter works". Reversing to
`feedbackUser.userId` + body text showed the same three records in the same order for every variant:
the parameter was being ignored. The hash differed because each item carried per-request volatile
fields (`preloadTime`, timestamps, request hints).

**Mechanism:** a whole-payload digest is sensitive to every field, including the ones the server
rewrites on each call, so it is **almost never equal** — which makes "params ignored" and "params
worked" produce the same observation ("hashes differ"). A test whose output is invariant across both
states of the claim is not evidence (see ㉑'s probe corollary).

**Fix:**
1. Pick **stable identity fields** (primary key, author id + timestamp, item URL) and compare the
   returned **sets/sequences**, not digests. Report the identity list beside each variant's verdict.
2. When a cursor field is present (`startIndex` / `nextStartIndex` / `hasMore`), read it too — but a
   cursor that stays at 0 is weak evidence on its own; the identity comparison is the decisive one.
3. A count read off the DOM or an API payload is a count of *nodes*, not of *records*. Dedupe by
   stable identity before writing "N items loaded" — container + inner-component selectors match the
   same record twice, so "3 → 6 after clicking" can mean nothing was loaded at all.

**Sibling shape — 200 with an empty payload is usually a block, not an absence.** Same schema, same
key count, no error message, but the payload collapses (observed: 72,701 → 1,505 bytes, `list: []`).
Before reporting "there is no data": compare `size` and key count against the last good response, and
state which of the two you are looking at. A silent throttle read as "empty" is how a soft block gets
reported as a negative finding.

### ㉙ Reviewing a plan or a prior claim: count, carrier, and declaration traps

**a) A count inside a plan is itself a claim — re-count it, and settle the UNIT first.**
A proposal phrased "generalize the 3 hardcoded paths" carries two assertions: the number, and the population it
counts. Grep the real file (`grep -n '<pattern>' <file>`) and print the enumeration, not the total. Observed: the
"3 places" were 1 place in the file plus 2 lines that merely contained the word (one already generic, one with no
path at all); "3" only became true when the unit was read as "3 copies × 1 place each". Different unit ⇒ different
files, different edits, different acceptance test. Restate count + unit + enumeration before touching anything.

**b) To decide "did this tool already check X?", grep the SOURCE — never one run's result file.**
A result file is one run's output, and a sibling probe's output says nothing about this probe. Observed: grepping the
second probe's result for a config key returned zero hits → "it never checked that" was reported to the user; the
first probe's source queried exactly that key and its result file had recorded the value. That is a **false
correction**, which costs as much trust as a false conclusion. Find the reporter's code (`grep -n '<key>|reg
query|Get-ItemProperty|Get-CimInstance' <script>`), then read its output.

**c) Before adding a restrictive declaration to a document, grep the body for instructions that contradict it.**
Appending "read-only checks only" to a skill whose body already requires running a minimal repair probe (a dry-run
`ALTER TABLE`, a SIGTERM reachability test) makes the document argue with itself, and the reader follows whichever
line they hit first. Either narrow the body (only state-preserving probes may run) or word the declaration to match
it ("state-changing operations require explicit user approval"). Verify by grepping the same file for the forbidden
verbs before and after the edit.

**d) Existence is not a path-test question for a file that is exclusively held.**
`Test-Path 'C:\pagefile.sys'` returns False while the file is present at full size — the memory manager holds it
open. Same family as ⑪: the probe reports the access result, not the inode. Decide existence with a directory
listing (`dir /a-d /a`, `ls -la <dir>`) and print the matched line with its size.

**e) A status field is only interpretable when its recorder was enabled.**
`BugcheckCode = 0` alongside `LogEvent = 0` and dumps disabled cannot distinguish "no crash occurred" from "a crash
occurred and nothing was written". Read the recorder's own switches first, then either state the weaker claim ("no
crash was *recorded*") or leave it unknown — never "confirmed there was none".

## Verification Checklist

- [ ] Every link in the reasoning chain has a type (fact/assumption/inference) and source
- [ ] At least one alternative explanation was considered and tested
- [ ] Every diagnostic claim passes the "what command verifies this?" test — **including every "don't do X" rationale**
- [ ] No delta-of-two-counts was used to infer a mechanism; insertion order or two snapshots was used instead
- [ ] The upstream chain was traced at least one level deeper than the initial finding
- [ ] No recent-change attribution was made without isolation testing
- [ ] Session_search results were confirmed against session endings, not mid-session snapshots
- [ ] Any "X does not work / returns nothing" claim was either localized to a data window or proven global by finding a positive case through the same parameter
- [ ] For every "tests pass" claim: an explicit check proves the exercised branch actually ran (not merely that it exited 0)
- [ ] For every "X changed M, so X had an effect" claim: a placebo/negative-control cut test was run, and the verdict is one of `attributable / cannot determine / data does not cover` — never "no effect" without positive evidence
- [ ] Every alert / crash notification was checked against the authoritative ledger **before** any causal explanation was written, and its outcome classified as `completed` / `failed` / `unknown` (never collapsed into "failed")
- [ ] Any long log line that carries the chained root cause was read untruncated, not filtered through `cut` / `head -c`
- [ ] Every "I measured it and it says X" link was checked against "does this proxy change when X changes?" — exec-time
      snapshots, fallback-branch message strings, and cached/derived files are not the object itself
- [ ] A "job/worker has not run" claim was re-checked against `last_run_at` + output artifact, not inferred from a
      sliding `next_run_at` or a single watchdog timeout line
- [ ] Every "this tool/platform cannot produce X" claim names the **non-default** option that was actually tried
      (custom range with start = end, advanced branch, export dialog), not just the default preset
- [ ] Every "nothing happened during this window" / "the loss was ≈ 0" claim was cross-checked against an
      independent ledger, after confirming the instrument was actually live across that window
- [ ] Every count presented as an event count (restarts, failures, reconnects) was matched on the **event verb or a
      structured field**, not on the object's name appearing in a log line — and the implied rate was sanity-checked
- [ ] Every "recovered / self-healed / transient" verdict was backed by **re-reading the same counter later in the
      session**, not by a success that merely followed a failure
- [ ] No total was summed across mixed bases; a printed "do not add these" caveat was followed by not adding them
- [ ] Every "steady / periodic / bursty / high" characterisation names the sampling window, the number of equal
      windows compared, and the baseline it was judged against — or states that no baseline exists
- [ ] Every probe's output was shown to differ between the claim's true and false states, with a known-positive and
      a known-negative case run side by side
- [ ] Every compound forecast (`baseline × %→ amount`) lists each factor as **measured** or **assumed**, uses a
      baseline window matching the claim's scope, and appears as a range rather than a single number when any factor
      is assumed
- [ ] Every propagated change names the **driver** of each term it touches; terms whose driver is not the thing
      being changed were held constant rather than perturbed by the same percentage
- [ ] Every "this parameter is / is not honoured" claim compared **stable identity** across variants
      (not whole-payload hashes, which differ on every call because of volatile fields), and any count
      quoted as "N items" was deduped by identity first (nested wrapper nodes double-count)
- [ ] Every "there is no data" claim was checked against a size/key-count delta versus the last good
      response, so a 200-with-empty-payload throttle is not reported as an empty result
- [ ] Where the data carries a **redundant encoding** of the classification you applied (a price tier that implies
      peak/off-peak, a status field that implies success, an index that implies ordering), your rule-derived split was
      cross-checked against it and matched to the unit — agreement validates the classifier for free, and a mismatch
      exposes it before the finding goes out
- [ ] Every count quoted from a plan or an earlier claim was re-derived by grep and printed with its **unit and
      enumeration** (file:line), not accepted as given
- [ ] A "this script/tool never checked X" claim was grounded in the script's **source**, not in one run's result file
- [ ] Any restrictive declaration added to a document was cross-checked against instructions in the same document
      that demand the opposite action — body and declaration edited together or not at all
- [ ] File existence was decided by a **directory listing**, not a path test, for files that may be exclusively held open
- [ ] Any status field used as evidence was first validated against the switches of the recorder that writes it
