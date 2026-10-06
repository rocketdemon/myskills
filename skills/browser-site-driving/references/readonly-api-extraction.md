# Read-only API evidence-gathering: get the whole site's data without clicking a single button (measured 2026-09-23)

> Companion to SKILL.md §3 (DOM runtime evidence-gathering), §3.5 (bundle static reconnaissance) and §4.5 (login-session persistence).
> This document adds a third — and the **most recommended** — way to pull data: **reading data via the site's own API**.
> (The SKILL.md body could not have this section's prose appended in that round because of dedup, so it stands alone as a document; a future foreground session can fold this file's key points into §3.6.)

## 0. When to use it

- The user says "**only look, read the information, don't change anything yet**" — in that case **don't click the UI**:
  an SPA's editor / playtest-style pages **may fire write requests as early as mount**.
  Case on this machine: the <target-site> "playtest module" has a cap of `playgroundMaxRoomsPerUserPerDay=1`,
  so going in very likely burns the day's one and only room-creation quota ⇒ it is an action that "writes data", so skip it outright.
- You need **structured ground truth** as the baseline for later scripts (DOM text is unsuitable as a baseline).
- You need to map out the platform's **write-API surface** (what to send to create/modify an object), for "rebuild item by item" tasks.

## 1. Endpoints come from asking the page, not from guessing

You can get the endpoints the current page just called even without subscribing to Network events — read `performance` (already deduped):

```python
js("JSON.stringify(performance.getEntriesByType('resource').map(e=>e.name)"
   ".filter(u=>u.includes('/api/')).map(u=>u.replace(location.origin,''))"
   ".filter((v,i,a)=>a.indexOf(v)===i))")
```

**Run it again on a different page** (list page → detail/builder → each sub-feature page) and the endpoints are essentially complete.
On this machine, three samples from `/apps` + `/project/{id}` + `/project/{id}/whiteboard` yielded
`/project/query-page`, `/project/tags?type=1`, `/project/factory/orders/by-project/{id}` and so on
(when sampling the first two pages, **not one of them was guessed**).

Then cross-check both ways against the bundle's static inventory (SKILL.md §3.5): `performance` gives "what was really called",
the bundle gives "what other capabilities exist".

## 2. The two-step way to get a response (`js()` doesn't await Promises)

Have the Promise write its result into `window`, then poll — more reliable than hoping `js()` awaits directly:

```python
js("window.__r=null; fetch(%s,{credentials:'include'}).then(r=>r.text())"
   ".then(t=>window.__r=t).catch(e=>window.__r='ERR:'+e)" % json.dumps(path))
for _ in range(24):
    time.sleep(0.5)
    v = js("window.__r")
    if v is not None:
        break
```

- To get the **status code**: `fetch(...).then(r=>r.text().then(b=>JSON.stringify({status:r.status,body:b})))`
  — used to distinguish `200 OK` / `405 Method Not Allowed` / `401`.
- `credentials:'include'` is required: it is what automatically carries the HttpOnly login cookie (§4.5).
- Give the polling cap plenty of room (start at 12s): a slow endpoint plus a large response shouldn't be misjudged as a timeout.

## 3. Write large responses to disk; don't pour them into the context

The code in `browser_exec` is just Python, and it can write **local** files directly — write to disk and print only a summary:

```python
OUT = "~/.hermes/research/<target-site>-data"; os.makedirs(OUT, exist_ok=True)
t = get(path)
open(os.path.join(OUT, name), "w", encoding="utf-8").write(t or "")
print("%-24s %7d bytes | top-level keys=%s" % (name, len(t or ""), list(json.loads(t).keys())))
```

- Print the **byte count + top-level keys + shape**; leave the field-level detail to a **local** analysis script you can run repeatedly (no token burn, reproducible).
- Directory convention for the dumps: `~/.hermes/research/<target-site>-data/` (ground-truth baseline + evidence archive),
  with a human-readable inventory written separately to `~/.hermes/research/<target-site>-project-inventory.md`.

## 4. Diagnosing two kinds of "looks like there's nothing" illusion

| Symptom | Truth | Handling |
|---|---|---|
| An endpoint's GET returns `Method Not Allowed` | 405 ≠ doesn't exist; the **method is just wrong** (case on this machine: `/project/{id}/tags` only accepts POST) | Switch methods, or switch to a GET endpoint that has that data; **don't conclude from this that "the platform doesn't have this feature"** |
| `document.cookie` / `localStorage` are both empty | HttpOnly cookies are invisible to JS (≠ not logged in) | Look at the landed URL or `cdp("Network.getCookies")` (SKILL.md §4.5) |

## 5. Write-operation payloads: read the call site in the bundle, don't guess

```bash
grep -roh ".\{340\}createProject(.\{340\}" /tmp/<target-site>_chunks | head -3    # ① UI call site
grep -roh "createProject:.\{0,120\}"         /tmp/<target-site>_chunks | head -3  # ② api client definition
```

Conclusion on this machine: `createProject: e => r.post("/project", e)` + the UI calling `createProject({name: t})`
⇒ **creating a project needs only `{name}`** (the remaining fields are filled in later by PATCHes from the builder).
⇒ **sending the same shape the web front end itself sends** is the only reliable starting point for scripted writes; on the live machine `code:0` passed.

**Same-family finds** (from the same api client): `patchProject:(id,t)=>r.patch('/project/'+id,t)`,
`updateProjectTags:(id,tags)=>r.post('/project/'+id+'/tags',{tags})`,
`createProjectRule:(id,body)=>r.post('/project/'+id+'/rules',body)`.

## 6. Independent re-check after a write operation (three steps, don't be lazy)

1. **Read the object back**: `GET` the object you just created/modified and check every field against expectations.
2. **Confirm nothing was collaterally damaged**: `GET` the list / the source object and compare for **unchanged** traces such as `update_time`.
   Case on this machine: after creating the new project, the source project's `update_time` was still the old value ⇒ the source was untouched (state this in the report).
3. **Reconcile quota/limits**: `GET /…/beta-limits` or the equivalent, confirming the remaining allowance changed as expected
   (on this machine: `maxProjectsPerUser=3`, and 2 were used after creating one).

## 7. The output shape from this measured run (copyable as-is)

- Raw JSON for 8 endpoints → `~/.hermes/research/<target-site>-data/`
- A human-readable inventory (basic info / tags / component tree 2 types·4 groups·24 items / 1 rule / production unsubmitted / whiteboard with no server-side data)
  → `~/.hermes/research/<target-site>-project-inventory.md`
- Creating a project live: `POST /api/v1/project {"name":"<project-name>RE"}` → `{"code":0,"data":{"id":"…"}}`
  → re-check: new project detail OK, list `total` 1→2, source project unchanged, 1 of the allowance left
