---
name: deep-research
description: Use when a request calls for deep analysis, root-cause diagnosis, or information research — route it to the right scenario, drill at least three layers deep, then summarize and run a conclusion-verification pass.
trigger: a complex analysis, diagnosis, research, or investigation request; keywords such as "why", "root cause", "underlying cause", "analyze this", "research", "troubleshoot", "dig to the bottom", "scout", "gather information", "look into", "what's out there", "how to do it"
---

# Deep Research Protocol

## Trigger conditions

Load this skill when the user's question meets any of the following:

- Diagnosis: "Why did X happen?" "What is the root cause?"
- Analysis: "Analyze Y." "What is going on with Z?"
- Troubleshooting: "Troubleshoot problem B." "Dig all the way down."
- Research: "Do a deep dive on A." "Scout C."
- Information: "What is out there in field D?" "How does E do it?" "What is the market situation for F?"
- Decision support: "Should I choose G or H?" "Is I worth doing?"

Not applicable to: simple lookups, status checks, single-step operations.

## Step 0: Scenario routing

**First decide which scenario the task belongs to**, then follow the matching drill path.

| Dimension | Scenario A: Deep root-cause diagnosis | Scenario B: Information research and value extraction |
|------|---------------------------|------------------------------|
| Core question | "Why did it happen?" | "What happened / what is the value?" |
| Reasoning direction | Vertical: symptom → cause → upstream cause | Horizontal: question → gather information → categorize → distill |
| Output form | A panoramic causal chain | Structured knowledge + actionable insight |
| Typical tasks | Crash investigation, timeout analysis, bug tracing | Technology research, market research, option comparison, trend analysis |
| Layer definition | Each layer traces one causal step further upstream | Each layer widens the information one level in breadth or depth |

**Decision rules:**

- If the task is essentially about explaining "why" → Scenario A
- If the task is essentially about learning "what it is / what exists / how to do it / which to pick" → Scenario B
- Mixed (both troubleshooting and research) → do A first, then B; after A's Step 3, decide whether B is needed

---

# Scenario A: Deep root-cause diagnosis

> The original troubleshooting flow, preserved in full.

## A.1 Task classification

First decide three dimensions:

### A.1.1 Difficulty

| Level | Characteristics | Expected rounds |
|------|------|---------|
| L1 Simple | Single-layer causality, answer domain known | 1-2 rounds |
| L2 Moderate | Multi-layer causality, cross-component analysis needed | 3-5 rounds |
| L3 Complex | Competing hypotheses, isolation testing needed | 5+ rounds |

### A.1.2 Type

| Type | Example question |
|------|---------|
| Fault diagnosis | "Why did X go down?" "Why the timeout?" |
| Performance analysis | "Why is it slow?" "Where is the bottleneck?" |
| Behavior explanation | "Why was it done this way?" "Why not that way?" |
| Trend research | "Why is the data growing?" "Cause of a pattern change" |
| Configuration archaeology | "Why was this config set this way?" "Reason for a historical change" |

### A.1.3 Domain & matching skill

Based on the components involved, pick the best-matching skill from the mapping below (pick 1, the most relevant):

| Problem domain | Matching skill |
|---------|-----------|
| Docker container lifecycle, start/stop, shutdown | a host-management checklist |
| ClickHouse faults, TTL, partitions, merges | a ClickHouse-operations checklist |
| MCP connections, tool-call failures | load the domain skill for that MCP, or search the local skills tree under `~/.hermes/skills/mcp/` for an MCP-related skill |
| The app/platform's own behavior | the platform's own agent skill |
| Python code bugs, logic errors | a systematic-debugging skill |
| Whole-infrastructure questions | an infrastructure-check checklist |
| Component upgrade/install | a component-lifecycle or component-upgrade assessment skill |
| Feishu (Lark) data operations | a Feishu Bitable inventory skill |
| Feishu MCP/bitable errors | a Feishu MCP/Bitable pitfalls reference (69 known traps) |
| Honcho API faults | a host-management checklist |
| Network/connection/DNS | a host-management checklist (network-resilience section) |

**If no skill matches**: mark it "no domain skill — research with generic methods" and go straight to A.2.

**How to load**: `skill_view(name="the matched skill name")`; after loading, follow its instructions to gather initial evidence.

## A.2 Layered drill (at least 3 layers)

**Definition of each layer**: question the previous layer's conclusion with "what caused this?" — trace one causal step further upstream.

### A.2.1 Initial layer (L0): symptom collection

After loading the domain skill (or using generic tools directly), collect surface symptoms:
- Logs, error messages, timeline
- Status of relevant components (container/service/process)
- Configuration and data differences

Output: **"Symptom: X behaves as Y at time T."**

### A.2.2 Layer 1: direct cause

Ask: what directly produced this symptom?

Output: **"L1 direct cause: A caused X."** + physical evidence

### A.2.3 Layer 2: upstream cause

Ask: what caused L1?

Output: **"L2 upstream cause: B caused A."** + physical evidence

### A.2.4 Layer 3 and beyond: keep going upstream

Each layer ask "what caused this?" until:

- **Hard boundary**: an unconfigurable system limit (a WSL 10s timeout, an HDD I/O bottleneck), an upstream bug (one that already has a GitHub issue), unobtainable data (requires external permission), or a physical limit (disk space)
- **Known root cause**: a factor already recorded in memory or in a previous session
- **Loop termination**: going deeper would only repeat information you already have

**Minimum 3 layers** (L0→L1→L2→L3). If you hit a hard boundary before layer 3, **do not pad the layer count** — instead verify that the hard boundary is real:

1. Confirm the same conclusion through a different tool path (e.g. docker inspect + journalctl + source comments)
2. Try a falsification test: "if this were not a hard boundary, what should I be able to see?"
3. Only after that passes, mark it a hard boundary and stop at the current depth

### A.2.5 Output format per layer

```
🔍 L{N}: {findings at this layer}

Evidence:
  - {evidence 1: command + output}
  - {evidence 2: log line + timestamp}

Is it a hard boundary: yes/no
  If yes: {hard-boundary type: system limit / upstream bug / unobtainable data / physical limit}
  If no: keep asking "what caused L{N}?"
```

**Every layer must be verified**: is the previous layer's "cause" really the cause (and not a coincidence in time)? Self-check: "would this cause really produce that result? Is there another equally reasonable explanation?"

## A.3 Summary

After the 3+ layer drill is complete, output a structured summary:

### A.3.1 Causal chain panorama

```
Symptom (L0): {specific symptom}
  ↓ because
L1 direct cause: {...}
  ↓ because
L2 upstream cause: {...}
  ↓ because
L3 root cause: {...} (hard boundary: {type})
```

### A.3.2 Per-layer confidence

| Layer | Finding | Confidence | Basis |
|----|------|--------|------|
| L1 | ... | High/Medium/Low | evidence / single-source / speculation |
| L2 | ... | High/Medium/Low | ... |
| L3 | ... | High/Medium/Low | ... |

"High" = multi-source evidence + isolation test
"Medium" = evidence present but alternative explanations not ruled out
"Low" = only temporal correlation or single-source inference

### A.3.3 Open questions

List the questions you discovered during the drill but could not answer (not an omission — an honest marking of boundaries).

## A.4 Conclusion-verification pass

Run a full conclusion-verification pass over the causal chain produced in A.3:

1. **Step 1 — Attack the reasoning chain**: classify every segment of the L0→L3 reasoning as Fact / Assumption / Inference
2. **Step 2 — Blind re-derivation**: collect evidence from scratch (using a different tool access path than round one) and derive independently to the same depth
3. **Consistency decision**:
   - Both rounds agree → ✅ conclusion confirmed
   - The two rounds disagree → run a third round (max 3 attempts)
   - Three rounds disagree → declare "cannot self-reconcile, human intervention required"

**This step may not be skipped**: a deep-research result that has not been through a conclusion-verification pass may not be declared complete.

---

# Scenario B: Information research and value extraction

> Suited to research, information-gathering, option-comparison, and trend-analysis tasks. Unlike Scenario A's vertical causal tracing, Scenario B follows the horizontal path of information breadth → depth → value.

## B.1 Scoping the research

Before gathering, define these boundaries:

| Dimension | Decision | Example |
|------|------|------|
| Core question | Define in one sentence what must be answered | "Main trends in the domestic tea wholesale market" |
| Information boundary | Time range, geographic range, source types | "Last 2 years, target market, industry reports + news + data" |
| Depth requirement | What each of L1-L3 needs | "L1 market overview, L2 competitive landscape, L3 actionable opportunities" |
| Availability forecast | Which information may be unobtainable — say so up front | "Online wholesale prices may be opaque; flag them" |

**Output**: a research-scope card (one sentence per dimension); **get the user's confirmation first** before gathering. This avoids wasted effort from a wrong direction.

## B.2 Multi-source information gathering

### B.2.1 Source strategy

Use at least **3 different types of source** per investigation:

| Source type | Tool | Suited to |
|---------|------|---------|
| Local-language search | a multi-provider web search tool (provider = bocha or zhipu) | local markets, policy, industry news |
| English/international search | a multi-provider web search tool (provider = serper, tavily) | technical docs, international comparison, academia |
| Vertical/domain | load the matching domain skill | Feishu data, database queries, logs |
| Conversation history | a conversation-history search tool | related content discussed before |
| System state | shell/terminal + data queries | local data, config, monitoring |

### B.2.2 Collection record format

After each search round, record:

```
📄 Source {N}: {URL/tool name}
  Type: {local search / English search / domain query / history / local}
  Key finding: {2-3 sentence excerpt}
  Credibility: High/Medium/Low (reason: {official/authoritative/individual/anonymous/speculation})
  Date: {publication date or data timestamp}
```

### B.2.3 Breadth vs depth trade-off

- **Round 1 (breadth-first)**: at least 5 distinct sources, to build the panorama quickly
- **Round 2 (targeted deep dive)**: for each key lead from round 1, drill to at least 2 additional sources
- **Round 3 (gap filling)**: check whether any important dimension is uncovered and search for it

## B.3 Categorization and organization

After gathering, organize the information with this framework:

### B.3.1 Thematic grouping

Group the gathered information by theme (no more than 5 themes), each group containing:
- A theme label (e.g. "market size", "competitive landscape", "technology route", "policy environment")
- Supporting evidence (cite the source numbers from B.2)
- Consistency assessment: is the information within the group consistent? Any contradictions?

### B.3.2 Contradiction identification

If information contradicts, neither hide it nor pick a side:

```
⚠️ Contradiction found:
  Source A ({URL}) says: {...}
  Source B ({URL}) says: {...}
  Suspected reason for the difference: {time lag / statistical basis / stance bias / one side outdated}
  Handling: {flag as unverified / trust the more authoritative side / present side by side}
```

### B.3.3 Information gaps

Honestly list what was not found or not covered:
- Dimension gap (e.g. "only online data found, no offline-channel data")
- Depth gap (e.g. "industry overview present, no company-level financials")
- Recency gap (e.g. "the latest data is from 2024, nothing for the following year yet")

## B.4 Value extraction (at least 3 layers)

Unlike Scenario A's vertical causal chain, Scenario B's "layers" are levels of information-value distillation:

### B.4.1 L1: Information summary ("what was found")

Extract key facts from the B.3 categorization. Tag the source of each.

Output format:

```
📊 L1 key facts:
  1. {fact} — sources {N},{N}
  2. {fact} — source {N}
  ...
```

**Self-check**: every item can be traced back to a specific source in B.2.

### B.4.2 L2: Pattern recognition ("how do the data relate to each other")

On top of the L1 facts, identify:
- Trends (rising/falling/cyclical)
- Correlations (A and B move together)
- Differences (X and Y behave differently under the same conditions)
- Consensus/disagreement (many sources agree vs. opinion is split)

Output format:

```
🔗 L2 patterns:
  Trend: "tea wholesale prices rose 5-8% per year over the last 2 years" — based on consistent data from sources 1,3,7
  Correlation: "online-channel growth coincides with loosening of livestreaming-commerce policy" — source 2's timeline aligns with source 5's policy milestones
  Disagreement: "report A sees a shrinking market, report B sees structural growth" — root cause of the difference: statistical basis (wholesale vs retail)
```

### B.4.3 L3: Deep insight ("what does this mean")

This is the most valuable layer. Distill actionable judgements from L1+L2:

- **Opportunity**: what can be done based on the trend
- **Risk**: adverse factors implied by the pattern
- **Causal judgement**: why the trend points in that direction (here you may borrow Scenario A's causal-chain method)
- **Actionable recommendation**: specific to "what to do, when to do it, why now"

Output format:

```
💡 L3 insights:
  Opportunity: "{specific opportunity}" — supported by: L1-{N} + L2 "{pattern name}" + source {N}
  Risk: "{specific risk}" — supported by: L2 "{contradiction/disagreement}" + sources {N},{N}
  Recommendation: "{executable recommendation}" — reason: {L3 insight reasoning}, timing: {why now}

  Confidence: {High/Medium/Low}
  Assumptions: {which assumptions that could change this insight depends on}
```

**Minimum bar**: L3 must produce at least one actionable insight. It may not stop at a level that cannot guide action, like "the market is big" or "competition is fierce".

## B.5 Cross-validation

### B.5.1 Multi-source validation of key conclusions

Every key insight in L3 needs at least **2 independent sources** of support. An insight supported by a single source must be flagged "single-source, unverified".

### B.5.2 Alternative-perspective check

For each L3 insight, think in reverse:

> "If this insight is wrong, what is the most likely reason?"

Write that reverse reasoning into "Assumptions" — if those assumptions do not hold, the insight must be re-evaluated.

### B.5.3 Validation methods

| Validation technique | Applies to |
|---------|---------|
| Re-search with different terms + a different search engine | validating information consistency |
| Check the raw data / primary source (not a retelling) | validating the accuracy of a second-hand interpretation |
| Compare data across multiple time periods | validating whether a trend persists |
| Look for opposing views / critical articles | validating whether there is selection bias |

## B.6 Uncovered areas and limitations

Honestly output:

```
⚠️ Limitations of this investigation:
  Not covered: "{specific dimension}" — reason: {information unobtainable / out of scope / language barrier}
  Confidence limited: "{specific insight}" — reason: {single source / outdated data / mostly speculation}
  Suggested follow-up: "{where to obtain this information if it is needed later}"
```

## B.7 Conclusion-verification pass

Run a conclusion-verification pass over the B.4-B.5 output.

### B.7.1 Attack the reasoning chain (equivalent to verification Step 1)

Break down the reasoning structure of **every L3 insight** item by item:

```
Insight: "{L3 insight text}"

  Fact (a fact directly supported by a source):
    - "{L1 fact}" — source {N},{N}
    - "{L1 fact}" — source {N}

  Assumption (a premise relied on but not source-verified):
    - "{assumption 1}" — why is this believed true? Any counterexample?
    - "{assumption 2}"

  Inference (a judgement derived from facts + assumptions):
    - "{L2 pattern}" → is this pattern a real trend or noise?
    - Alternative explanation: "{if this phenomenon had another cause, what would it be?}"
```

**Decision**:
- If Assumptions outnumber Facts → confidence drops one level automatically
- If an Inference depends on an Assumption that is "common sense but unverified" → flag it as a risk point

### B.7.2 Blind re-derivation (equivalent to verification Step 2)

From scratch, independently validate the core conclusions using a **different search strategy**:

| What round 1 used | What round 2 should switch to |
|-------------------|------------|
| Bocha local-language search | Zhipu search / Metaso search |
| Used "white tea wholesale 2024" | Switch to "Fuding white tea market conditions" or the English "white tea wholesale China" |
| Searched industry reports | Search news / policy documents / academic papers |
| Searched only in the local language | Add a round of English search |
| Used a multi-provider web search tool | Use a browser to visit the key sources directly and verify the primary data |

**Key difference**: Scenario A's blind re-derivation changes the system tool path (docker inspect → journalctl → source code); Scenario B changes the search tool, the search terms, and the source language/type. **Switching only the search terms without switching the search engine → false independence**.

### B.7.3 Consistency decision

Compare the L3 insights of the two rounds:

| Result | Decision | Handling |
|------|------|------|
| Core insights agree across both rounds | ✅ Pass | Conclusion confirmed, deliver |
| Same direction but details differ | ⚠️ Minor adjustment | Flag the differences, trust the parts both rounds confirm |
| An insight finds no support in round 2 | ❌ Doubtful | Demote that insight to "speculation", flag the risk |
| Overall conclusions contradict | ❌ Fail | Run a third round (max 3), and declare "cannot self-reconcile" if three rounds disagree |

**Minimum bar**: every L3 insight marked "high confidence" must pass two-round validation. Medium/low-confidence insights must at least have their differences flagged.

---

# Common traps (shared by both scenarios)

## Trap list

### ① Declaring a root cause at the first layer (Scenario A)

**Symptom**: finding that A is the cause → immediately declaring "the root cause is A" without asking "what caused A?"

**Correction**: the first layer is only the direct cause, not the root cause. Keep asking "why".

### ② "Hard boundary" declared too early (Scenario A)

**Symptom**: hitting a slightly difficult obstacle and declaring it a "hard boundary", when in fact you just do not want to dig deeper.

**Correction**: a hard boundary must satisfy:
- System limit: documented/source/config-proven to be immutable
- Upstream bug: has a GitHub issue link
- Unobtainable data: a clear permission boundary (not "hard to get" but "impossible to get")

"Hard to look up" ≠ "hard boundary".

### ③ Wrong domain skill chosen

**Symptom**: picking an irrelevant skill, then shoving the irrelevant evidence it collects into the conclusion.

**Correction**: before loading, confirm the skill's description matches the problem domain. If it does not match, switch — do not force-fit.

### ④ Causal chain / information chain missing evidence

**Symptom**: L1 says "A caused B" with no log/state-change/timestamp support; or an L2 pattern is recognized with no supporting data points.

**Correction**: at least one reproducible piece of evidence per layer (command + output, log line, search-result URL + excerpt). A reasoning step with no evidence is flagged "inference", not "fact".

### ⑤ Conclusion-verification skipped

**Symptom**: Scenario A finishes the three-layer drill without running A.4; Scenario B finishes the three-level distillation without running B.7 — then declares completion.

**Correction**: A.4 and B.7 are not optional. Even if the agent thinks its conclusion is "obvious", it must still run a conclusion-verification pass — this is a guard against your own cognitive blind spots. Scenario A changes the tool path in re-derivation, Scenario B changes the search strategy; the principle is the same.

### ⑥ Reusing the tool path causes false independence

If, in the Step 4 blind re-derivation, you change the tool but not the reasoning direction → false independence. See the reasoning-strategy comparison table in Step 2 of the conclusion-verification discipline (5 strategy switches + false-independence test cases).

### ⑦ Information gathering stops at the first page (Scenario B)

**Symptom**: searching one keyword, grabbing the top 5 results, and declaring "research complete".

**Correction**: B.2 requires at least 3 source types × 3 search rounds (breadth → depth → gaps). One search-results page is not research.

### ⑧ Value extraction stops at the summary (Scenario B)

**Symptom**: L1/L2 done (information summary + pattern recognition), L3 (deep insight) skipped, then delivered.

**Correction**: L3 is Scenario B's core deliverable. If the L3 output cannot guide the user to a concrete decision — "what to do now / what not to do" — the research is not complete.

### ⑨ The "silent inconsistency" family in quantitative deliverable tables (Scenario B; always check when producing a comparison/scoring/segmentation table)

**Symptom**: when a deliverable table is assembled from multiple scripts/files, three "non-erroring but wrong" forms appear — the output looks normal, but a conclusion has been silently eaten.

| Variant | Mechanism | Criterion |
|------|------|------|
| ① Category enumeration out of sync with consumers | A new bucket is added (e.g. `host-limited`) but the aggregation script / summary / rendering ordinal table is not updated | the bucket's entries **silently vanish** from all statistics, and "sum of parts ≠ total" without raising an alarm |
| ② Cross-file primary-key mismatch | The scoring table uses `modelscope` while the candidate list uses `modelscope-mcp` ⇒ the lookup fails and falls into the `default` bucket | a lookup miss is silently absorbed by `default` |
| ③ Evidence text out of sync with the numeric field | The evidence text says "raised 2→3" but the score field is still 2 | the report **contradicts itself** (the prose says up, the table shows not up) |

**Correction**:

- Every classification/enumeration must have a **single source** (one constant definition that all consumers reference); do not let each script write its own copy
- Any `dict.get(k, default)` fallback **must leave a trace**: print a warning and list the key on a miss; silent downgrading is forbidden
- The output must forcibly print a **reconciliation line**: `sum of parts == total` (raise an alarm and list the excluded categories when it does not hold)
- **Every number appearing in the report (total / coverage / top score / bottom score) must be mechanically checked by a script against the artifact files**; mental arithmetic and impressions are forbidden. The reconciliation script itself can also have a field name wrong (e.g. writing `candidates` where it should be `channels`) ⇒ on the first run you must verify it against the items that do have numbers; do not trust "it passed on the first try"

### ⑩ Scenario misjudgement

**Symptom**: a research task goes down Scenario A, searching for a "root cause" with the causal-chain method for ages and finding none; or a troubleshooting task goes down Scenario B, gathering a mountain of irrelevant information.

**Correction**: the Step 0 scenario routing must be judged carefully. If unsure, spend 30 seconds confirming — ask the user "do you want to figure out why this is happening, or to understand what exists in this field?"

---

# Completion criteria

**Scenario A completion criteria**:

- [ ] A.1: task classified (difficulty / type / domain)
- [ ] A.1: domain skill loaded (or marked as no match)
- [ ] A.2: at least 3 layers drilled, each with evidence
- [ ] A.2: a hard boundary reached, or loop termination
- [ ] A.3: causal-chain panorama + confidence table output
- [ ] A.4: conclusion-verification pass passed (two rounds agree / three-round majority / human intervention)

**Scenario B completion criteria**:

- [ ] B.1: research-scope card defined and confirmed by the user
- [ ] B.2: at least 3 source types, 3 search rounds complete, each with a record
- [ ] B.3: categorization done (thematic grouping + contradiction identification + gap flags)
- [ ] B.4: three-level distillation done, L1 (facts) → L2 (patterns) → L3 (insights)
- [ ] B.4: L3 produces at least 1 actionable insight
- [ ] B.5: key insights validated by at least 2 independent sources
- [ ] B.6: uncovered areas and limitations flagged
- [ ] B.7: conclusion-verification pass passed (attack the reasoning chain + blind re-derivation + consistency decision)
- [ ] B.7: the blind re-derivation **actually ran** (a different search engine, not just different search terms) — a tool being unavailable or an engine returning irrelevant results ≠ validation passed; you must switch to a working engine and re-run, and honestly disclose any path that could not be run
- [ ] Quantitative deliverables mechanically reconciled: sum of parts == total, cross-file primary keys consistent, report numbers vs artifact files zero diff (see trap ⑨)
