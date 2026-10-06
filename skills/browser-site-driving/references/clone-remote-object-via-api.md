# Cloning a remote object: the "plan → execute → re-verify" loop (when the platform has no "copy" feature)

> Applies when: the user wants "an exact copy of my existing X" (project / board / document / config / workspace),
> and the platform **has no** copy/clone feature (criterion: zero keyword hits in SKILL.md §3.5).
> Means = with the logged-in session, **call the official API item by item via in-page fetch** (endpoint discovery: see `references/readonly-api-extraction.md`,
> payload shape: see its §5 + SKILL.md §3.5). First complete live run: <target-site> "<project-name>" → "<project-name>RE", 2026-09-23.
>
> ⚠️ **The user's stated requirement (made explicit on 2026-09-23; a hard requirement)**: it must be done as the loop **plan first → then execute → then re-verify**,
> **the problems found by each round's re-verification are the input to the next round**, until re-verification says "the two sides are identical apart from the name".
> Don't "write it once and report done"; and don't skip re-verification.

## 1. Plan: first list the "comparable positions" + the equivalence criterion for each

The planning stage's output must be a **checklist** (not a feeling): break the source object into N positions and define "how to judge it identical" one by one.
Don't move on to execution before the plan is complete — otherwise you only find out during re-verification that "this position never had a definition of identical".

| Position type | Equivalence criterion |
|---|---|
| Scalar fields (name/description/headcount/duration/status…) | compare field by field with `JSON.stringify`; **first explicitly exclude the fields that are "necessarily different by design"**: `id`, `owner/created_by`, `create_time/update_time`, object-level id, attachment id/URL, and (in this case) the name itself |
| Child-object collections (groups/items/tasks…) | pair by **business key** (name or business key) → compare counts (missing/extra) + **in-group order** + every field |
| Ordering/status | compare `sort_order` and `status` entry by entry (order is part of the content too) |
| Binary attachments (images/files) | **compare the sha256 of the content byte by byte** — re-uploading necessarily produces a new id/URL, so comparing id/URL gives nothing but false differences |
| Derived / redundant fields (plain-text summaries, cached text…) | first determine "sent by the front end or recomputed server-side" (see the probe in §2); when comparing, give a **normalization rule** (strip whitespace) **and also report the original length** |
| Platform-side empty states (whiteboard / unsubmitted order / playtest with no records) | fetch once on both sides and confirm they **are empty in the same shape** (in this case: whiteboard has no server-side data, production order is `not_submitted` on both, game records 0=0) |

## 2. Execute: order, idempotency, receipts

- **Dependency order**: parent → child → attachments (create the project/container first, then the groups, then the child items, and only then the attachments and ordering).
- **The parent id must be the target's**: the source entity's `group_id`/`parent_id` does not exist in the target (in this case it reported `组件分组不存在`).
  Platform-level **shared types** (system types) have the same id on both sides and can be reused directly.
- **The fields a multipart create accepts are a whitelist**: in this case `createComponentItem` only accepts
  `project_id / type_id / group_id / name / description / qty / [image_id]`, and does **not accept `sort_order`**
  ⇒ after creating, you must `PATCH` in `sort_order`, otherwise it defaults to creation order (re-verification will then report "order differences" en masse).
- **Re-uploading attachments**: the source URL is often a **pre-signed link** (in this case `X-Amz-Expires=259200` = 3 days) ⇒ do it **on the spot**:
  `fetch(srcUrl) → blob → FormData('file', blob, 'image.png') → POST /upload/image` → get the new id → create/patch the object.
- **try/catch around each item + persist the id mapping to disk**: one failure must not interrupt the whole batch; the log records `src_id → new_id`
  (in this case written to `id-map-*.json`); later re-verification and "backfill by gap" both rely on it.
- **Backfilling gaps is always based on the target's current state** (see pitfall 1 in §4).

## 3. Re-verify: mechanical comparison that produces a difference list

- **Re-fetch the source on every re-verification** (don't reuse the snapshot from the start — see pitfall 2 in §4).
- Comparison dimensions = every item of the plan's checklist: scalars / child-object counts and order / every field / attachment sha256 / normalized derived fields.
- Output shape: for each item, give **counts and a list** for "missing / extra / field differences / order differences / attachment mismatches"; write `✅` for `0`.
- **The difference list becomes the next round's input directly**: one difference → one fix action → run the same re-verification again.
- Termination condition: **re-verification shows 0 differences** (apart from the "necessarily different by design" fields).

## 4. Two process pitfalls actually hit in practice (both can make "0 differences" an illusion)

1. **The criterion used the source instead of the target**: the first backfill script decided whether the target already existed from "the name already present in the source" ⇒ always true ⇒
   **0 items were backfilled, and the log looked like "no gaps"**. ⇒ every boolean "should this be created?" criterion inside the loop must be reconcilable
   (print the basis for the decision and the conclusion), otherwise "backfilled successfully" and "never backfilled at all" look identical in the output.
2. **The baseline drifts**: the source object can be modified **elsewhere** while the copy is in progress (in this case: 3 Token entries were added to the source project,
   its object-level `create_time` = 2 minutes after the copy began).
   ⚠️ **the parent object's `update_time` does not necessarily change** ⇒ you **must not** use it to prove "the source wasn't touched";
   use the **child objects' create/update_time** to judge the increments item by item.
   ⇒ the report must state "the copy baseline = the source state at the moment of re-verification", and explain in the differences that "the source has N extra" is an **external change**.

## 5. Determining "was the field sent by the front end or recomputed server-side" — the three-round write probe

Write an **empty string** → read back; write a **junk string** (`__PROBE__XYZ`) → read back; write a **normal value** → read back.

- If both the empty string and the junk string are **stored and returned as-is** ⇒ the server does not derive the value; the client is in charge.
- When it is **submitted together with a sibling field** the value gets rewritten (in this case: `PATCH /rules/{rid}` carrying `content` as well ⇒ the backend recomputes
  `content_text`, 1870 chars in the source → 1793 chars, paragraph separators going from blank lines to single newlines) ⇒ the server recomputes under that combination.
  ⇒ **to keep the source's original value, send only that field** (in this case sending only `content_text` ⇒ byte-for-byte equal to the source).
- The probe uses your own target object; **don't experiment on the source object** (the source is the user's asset; don't write to it without permission).

## 6. Reporting format (the shape the user accepted)

1. **Tell it by loop number**: what round N's re-verification found (with evidence) → what the next round fixed → the final-verification result.
2. **Give a table for the final verification**: position / comparison method / result (write ✅ for 0 differences).
3. **Give a separate section for "necessarily different by design"**: name (e.g. `X` → `XRE`), `id`, owner, create/update times, object id,
   attachment id/URL — so the user doesn't think the copy was incomplete.
4. **The boundary trio**: ① did anything write to the source object (evidenced by `update_time` / object timestamps) ② a baseline-drift warning
   ③ positions not checked and why (e.g. the read-only stage **deliberately skips** entry points that write data: in this case the "playtest module" was skipped because
   `playgroundMaxRoomsPerUserPerDay=1` could consume the day's room-creation quota).
5. After a long copy-type task finishes, **suggest crystallizing a skill** (generic process goes into SKILL.md / this document, site specifics go into `references/<target-site>.md`).

## 7. Ready-made scripts (don't rewrite them each time)

| Script | Purpose | Status |
|---|---|---|
| `scripts/verify-project-clone.js` | Re-verifier: item-by-item source vs target comparison → human-readable report (missing/extra/order/fields/image sha256/derived-text original length + normalized/platform empty states) | **Actually tested** (the <target-site> final verification was produced by it) |
| `scripts/clone-project-to-target.js` | Cloner: read the source → idempotently write each item to the target (parent id from the target, image download + re-upload, sort_order backfill, derived fields PATCHed separately) | **DRY read-only planning actually tested**; the write path is implemented from the bundle source and **has not been run end to end** |
| `scripts/fetch-spa-chunks.sh`, `scripts/analyze-spa-bundle.py` | Endpoint discovery: fetch the SPA build + extract baseURL/routes/endpoints/copy | Actually tested |
| `scripts/probe-cookie-persistence.py` | Login-session persistence check (reads a copy of the Cookies store) | Actually tested |

**Call shape** (inside `browser_exec`, with the page already logged in on the same origin):

```python
P = "~/.hermes/skills/<this-skill>/scripts/verify-project-clone.js"
c = open(P).read().replace("__SRC_ID__", SRC).replace("__DST_ID__", DST)
print(js(c))                       # re-verify

c2 = open(P.replace("verify-project-clone", "clone-project-to-target")).read() \
       .replace("__SRC_ID__", SRC).replace("__DST_ID__", DST)   # default DRY=true: read-only planning
print(js(c2))
# once confirmed, write: c3 = c2.replace("DRY: true", "DRY: false")
```

- Switching sites requires changing only three things: the **id semantics** (which fields are parent ids), the **field whitelist** (what multipart accepts),
  and the **"necessarily different by design" field table** (the exclusions).
- **Running DRY first** is a hard habit: it verifies at the same time that the "read side + pairing logic" is correct (e.g. on an already-fully-copied project it should output
  `新建 0 / 已存在 N`), and it won't write dirty data because of a wrong judgment.
- After the write path has been run for real the first time, backfill the pitfalls found in real responses into this document and the site references (especially **response field names**;
   this time it was pinned down from `.data.image_id` in the bundle source rather than guessed).
