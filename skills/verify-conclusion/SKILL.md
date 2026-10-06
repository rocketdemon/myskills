---
name: verify-conclusion
description: Use when the agent has just produced a diagnostic conclusion, research finding, or any inference from collected evidence. Systematically stress-tests the reasoning chain and re-derives from scratch to catch premature conclusions, unverified assumptions, and causal fallacies.
version: 1.1.0
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

## When to Use

**Trigger immediately after** the agent produces any of:
- A diagnostic conclusion ("X caused Y", "root cause is Z")
- A research finding or synthesis
- An inference from collected clues/evidence
- Any claim marked as `关键结论` (key conclusion) or `根因` (root cause)

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
5. **Fix feasibility check:** Does the diagnosis imply a repair action (ALTER TABLE, config change, etc.)? If yes, run the most lightweight validation that confirms the fix can be applied before declaring the diagnosis complete. A diagnosis whose repair path is blocked is an incomplete diagnosis — flag it and identify the blocker. Examples: before declaring "add TTL" as the fix, run ALTER TABLE with a low-timeout dry-run; before declaring "restart service X" as the fix, check that the service can respond to SIGTERM. Do NOT defer this check to the implementation phase — a fix that can't be applied invalidates or constrains the diagnosis.

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

### ④ Reflexive attribution to recent changes

**Pattern:** Any recent config change, upgrade, or modification is reflexively blamed for any new symptom. The upgrade WAS the most recent change, but that doesn't make it the cause.

**Fix:** List at least one alternative explanation that does NOT involve the recent change. Attempt to falsify it.

### ⑤ Intermediate state reported as final conclusion

**Pattern:** A session_search DISCOVERY result shows "X is pending" in the middle of a session, but the same session's bookend_end resolved it. Agent reports the pending state without checking the end.

**Fix:** After any lookup (especially session_search), always check whether the same source later updated or invalidated the finding.

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

**Real case (2026-07-27):** Agent asserted `/etc/sudoers.d/hermes-journal` had been deleted → inferred "the Phase 4 fix never ran" → after the user challenged it, re-inspection showed `ls -la /etc/sudoers.d/` listing the file all along, and `sudo -n journalctl --flush` returning 0. The root cause was not a missing file but the permission-limited `sudo cat`.

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

## Verification Checklist

- [ ] Every link in the reasoning chain has a type (fact/assumption/inference) and source
- [ ] At least one alternative explanation was considered and tested
- [ ] Every diagnostic claim passes the "what command verifies this?" test
- [ ] The upstream chain was traced at least one level deeper than the initial finding
- [ ] No recent-change attribution was made without isolation testing
- [ ] Session_search results were confirmed against session endings, not mid-session snapshots
