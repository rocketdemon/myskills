/*
 * <target-site> project cloner —— write the source project's "content" into the target project item by item
 *
 * ⚠️ Status statement (honest): **the read side + planning side have been tested for real** (DRY mode has been run on real data);
 *    **the write side was written from the bundle source + measured API shapes, but has never been run end-to-end under a real quota** (account `maxProjectsPerUser=3`,
 *    only 1 slot was left at the time, and it should not be taken up for testing). On the first real run please: ① DRY first to see the plan ② right after it finishes use
 *    `scripts/verify-project-clone.js` to re-check ③ the difference list is the input for the next round of fixes (the loop the user asked for).
 *
 * Usage (inside browser_exec; the page must already be logged in):
 *   P = "~/.hermes/skills/<this-skill>/scripts/clone-project-to-target.js"
 *   c = open(P).read().replace("__SRC_ID__", SRC).replace("__DST_ID__", DST)
 *   print(js(c))                       # CFG.DRY=true → read-only planning (safe)
 *   # to confirm you really want to write: c2 = c.replace("DRY: true", "DRY: false") then run that
 *
 * API shape evidence (verbatim from the SPA bundle `creator-inbox-bell-*.js` / `project-*.js`):
 *   uploadProjectImage(id,file,oldId?)  → POST /project/{id}/upload/image  FormData: file[,old_image_id]
 *                                        where the response is consumed: `.data.image_id` / `.data.image_url`
 *   createComponentItem(FormData)       → POST /project/component/item    whitelisted fields:
 *                                        project_id,type_id,group_id,name,description,qty[,image_lofi_id][,image_hifi_id]
 *                                        **does not include sort_order** ⇒ after creating you must PATCH /project/component/item/{id} {sort_order}
 *   createComponentGroup                → POST /project/component/group  {project_id,type_id,name,sort_order}
 *   updateProjectTags(id,tags)          → POST /project/{id}/tags         {tags:[<id string>…]}
 *   createProjectRule / updateProjectRule → POST /project/{id}/rules | PATCH /project/{id}/rules/{rid}
 *
 * Idempotency: every step **takes the target's current state as the source of truth** (present in the source ≠ present in the target); skip what already exists, write only when a field differs.
 * Parent ids are always taken from the **target** project (source group_id/type_id do not exist in the target → 400 "component group does not exist").
 */
(async () => {
  const CFG = {
    SRC: "__SRC_ID__",
    DST: "__DST_ID__",
    DRY: true,          // true = read-only planning, no write request is sent
    WITH_IMAGES: true,  // download the source image → re-upload it to the target (the presigned URL is valid for 3 days, so it must be done on the spot)
    COPY_NAME: false,   // by default the project name is not copied (the target usually already has its own name)
  };
  const B = "/api/v1";
  const L = [], MAP = { groups: {}, items: {}, images: {}, rules: {} };
  const say = (s) => L.push(s);
  const tag = CFG.DRY ? "PLAN" : "WRITE";

  const j = async (u, opt) => {
    try {
      const r = await fetch(u, Object.assign({ credentials: "include" }, opt || {}));
      const t = await r.text();
      try { return { http: r.status, body: JSON.parse(t) }; } catch (e) { return { http: r.status, body: { __raw: t.slice(0, 200) } }; }
    } catch (e) { return { http: 0, body: { __err: String(e) } }; }
  };
  const jpost = (u, b) => j(u, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(b) });
  const jpatch = (u, b) => j(u, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(b) });
  const send = async (desc, fn) => {                    // unified write gate: in DRY mode only the plan is printed
    if (CFG.DRY) { say("  " + tag + " " + desc); return null; }
    const r = await fn();
    say("  " + tag + " " + desc + " → http " + r.http + (r.body && r.body.code !== undefined ? " code=" + r.body.code : "") +
        (r.body && r.body.msg && r.body.msg !== "success" ? " msg=" + r.body.msg : ""));
    return r;
  };

  const S = await j(B + "/project/" + CFG.SRC), D = await j(B + "/project/" + CFG.DST);
  if (!S.body.data || !D.body.data) return "FAIL could not fetch project: " + JSON.stringify(S.body).slice(0, 200) + " / " + JSON.stringify(D.body).slice(0, 200);
  const ps = S.body.data, pd = D.body.data;
  say("== clone" + (CFG.DRY ? "[DRY read-only plan]" : "[WRITE real writes]") + ": source " + CFG.SRC.slice(0, 8) + " → target " + CFG.DST.slice(0, 8) + " ==");

  // ---------- 1. basic info (write only the fields that differ from the target) ----------
  const BASE = ["description", "players_min", "players_max", "duration_min", "duration_max", "target_age_min"];
  if (CFG.COPY_NAME) BASE.push("name");
  const baseBody = {};
  BASE.forEach((k) => { if (JSON.stringify(ps[k]) !== JSON.stringify(pd[k])) baseBody[k] = ps[k]; });
  if (Object.keys(baseBody).length) {
    for (const k of Object.keys(baseBody)) MAP.base[k] = baseBody[k];
    const r = await send("PATCH /project/{target} " + JSON.stringify(baseBody), () => jpatch(B + "/project/" + CFG.DST, baseBody));
    delete MAP.base;                                   // basic info has no id mapping, succeeding is enough
  } else say("  PLAN basic info already identical, skipping");

  // ---------- 2. tags (array of id strings) ----------
  const sTags = (ps.boardgame_type_tags || []).map((t) => t.id);
  const dTags = (pd.boardgame_type_tags || []).map((t) => t.id);
  if (JSON.stringify(sTags) !== JSON.stringify(dTags)) {
    await send("POST /project/{target}/tags " + JSON.stringify(sTags), () => jpost(B + "/project/" + CFG.DST + "/tags", { tags: sTags }));
  } else say("  PLAN tags already identical, skipping");

  // ---------- 3. groups ----------
  const tS = (await j(B + "/project/" + CFG.SRC + "/component-tree")).body.data || [];
  const tD = (await j(B + "/project/" + CFG.DST + "/component-tree")).body.data || [];
  const byName = (arr, n) => (arr || []).find((x) => x.name === n);
  for (const ts of tS) {
    const td = byName(tD, ts.name);
    if (!td) { say("  ⚠️ target is missing component type \"" + ts.name + "\" (a custom type must first be created with POST /project/component/custom-type), skipping this type"); continue; }
    for (const gs of ts.groups || []) {
      const gd = byName(td.groups, gs.name);
      if (gd) {
        MAP.groups[gs.id] = gd.id;
        if ((gs.sort_order !== gd.sort_order) || JSON.stringify(gs.pg_default_config_json) !== JSON.stringify(gd.pg_default_config_json))
          await send("PATCH /project/component/group/{target" + gd.id.slice(0, 8) + "} {sort_order:" + gs.sort_order + "}", () => jpatch(B + "/project/component/group/" + gd.id, { sort_order: gs.sort_order }));
        continue;
      }
      const r = await send("POST /project/component/group \"" + ts.name + "/" + gs.name + "\" sort=" + gs.sort_order,
        () => jpost(B + "/project/component/group", { project_id: CFG.DST, type_id: td.id, name: gs.name, sort_order: gs.sort_order || 0 }));
      if (r && r.body && r.body.data) MAP.groups[gs.id] = r.body.data.id;
    }
  }

  // ---------- 4. items (including image re-upload) ----------
  const upImage = async (srcUrl, what) => {
    if (CFG.DRY) { say("  PLAN upload image (" + what + ") ← " + srcUrl.slice(0, 60) + "…"); return null; }
    const br = await fetch(srcUrl, { credentials: "include" });          // presigned S3 URL: measured to be readable cross-origin (a same-page fetch has succeeded)
    if (!br.ok) { say("  ⚠️ failed to fetch source image HTTP" + br.status + " (" + what + ")"); return null; }
    const blob = await br.blob();
    const fd = new FormData();
    fd.append("file", blob, "image.png");
    const r = await fetch(B + "/project/" + CFG.DST + "/upload/image", { method: "POST", credentials: "include", body: fd });  // do not hand-write Content-Type
    const b = await r.json().catch(() => ({}));
    const id = b && b.data && b.data.image_id;                           // evidence: the bundle consumes .data.image_id
    say("  WRITE upload image " + what + " → http " + r.status + " image_id=" + (id ? id.slice(0, 8) : "(not obtained, check the response shape)"));
    if (id) MAP.images[srcUrl.slice(-24)] = id;
    return id;
  };

  let created = 0, skipped = 0;
  for (const ts of tS) {
    const td = byName(tD, ts.name); if (!td) continue;
    for (const gs of ts.groups || []) {
      const gdId = MAP.groups[gs.id] || (byName(td.groups, gs.name) || {}).id;
      if (!gdId) continue;
      const dItems = ((byName(td.groups, gs.name) || {}).items) || [];
      for (const it of gs.items || []) {
        const ex = dItems.find((x) => x.name === it.name);
        if (ex) { skipped++; MAP.items[it.id] = ex.id; continue; }
        created++;
        const lofi = (CFG.WITH_IMAGES && it.image_lofi_url) ? await upImage(it.image_lofi_url, "lofi/" + it.name) : null;
        const hifi = (CFG.WITH_IMAGES && it.image_hifi_url) ? await upImage(it.image_hifi_url, "hifi/" + it.name) : null;
        if (CFG.DRY) { say("  PLAN create item \"" + ts.name + "/" + gs.name + "/" + it.name + "\" qty=" + it.qty + " image=" + (it.image_lofi_url ? "yes" : "no") + " sort=" + it.sort_order); continue; }
        const fd = new FormData();
        fd.append("project_id", CFG.DST);
        fd.append("type_id", td.id);                                     // target type id
        fd.append("group_id", gdId);                                     // target group id
        fd.append("name", it.name);
        fd.append("description", it.description || "");
        fd.append("qty", String(it.qty));                                // whitelist: does not include sort_order
        if (lofi) fd.append("image_lofi_id", lofi);
        if (hifi) fd.append("image_hifi_id", hifi);
        const r = await fetch(B + "/project/component/item", { method: "POST", credentials: "include", body: fd });
        const b = await r.json().catch(() => ({}));
        const nid = b && b.data && b.data.id;
        say("  WRITE create item \"" + it.name + "\" → http " + r.status + " id=" + (nid ? nid.slice(0, 8) : "(failed: " + JSON.stringify(b).slice(0, 120) + ")"));
        if (!nid) continue;
        MAP.items[it.id] = nid;
        if (nid) await send("PATCH /project/component/item/{target" + nid.slice(0, 8) + "} sort_order=" + it.sort_order,
          () => jpatch(B + "/project/component/item/" + nid, { sort_order: it.sort_order }));
      }
    }
  }

  // ---------- 5. rules (content goes as a JSON string; content_text is PATCHed separately so the backend does not recompute it) ----------
  const rS = (await j(B + "/project/" + CFG.SRC + "/rules")).body.data || [];
  const rD = (await j(B + "/project/" + CFG.DST + "/rules")).body.data || [];
  for (const rs of rS) {
    const rd = rD.find((x) => x.title === rs.title);
    const content = typeof rs.content === "string" ? rs.content : JSON.stringify(rs.content);
    if (!rd) {
      const r = await send("POST /project/{target}/rules \"" + rs.title + "\"", () => jpost(B + "/project/" + CFG.DST + "/rules",
        { title: rs.title, content: content, content_text: "", sort_order: rs.sort_order || 0 }));
      const nid = r && r.body && r.body.data && r.body.data.id;
      if (nid) MAP.rules[rs.id] = nid;
      if (nid) await send("PATCH /rules/{target" + nid.slice(0, 8) + "} {content_text} (sent separately, to avoid recomputation)",
        () => jpatch(B + "/project/" + CFG.DST + "/rules/" + nid, { content_text: rs.content_text }));
    } else {
      MAP.rules[rs.id] = rd.id;
      if (JSON.stringify(rs.content) !== JSON.stringify(rd.content))
        await send("PATCH /rules/{target" + rd.id.slice(0, 8) + "} {content}", () => jpatch(B + "/project/" + CFG.DST + "/rules/" + rd.id, { content: content }));
      if ((rs.content_text || "") !== (rd.content_text || ""))
        await send("PATCH /rules/{target" + rd.id.slice(0, 8) + "} {content_text} (sent separately)", () => jpatch(B + "/project/" + CFG.DST + "/rules/" + rd.id, { content_text: rs.content_text }));
    }
  }

  say("");
  say("Plan summary: items created " + created + " / already existing " + skipped + "; group mappings " + Object.keys(MAP.groups).length +
      "; rules " + rS.length + " (source) / " + rD.length + " (target)");
  say("id mappings (kept on disk as a record; both the re-check and \"fill by gaps\" rely on them): " + JSON.stringify(MAP));
  say("Next step: run scripts/verify-project-clone.js to re-check; **the difference list = the input for the next round of fixes** (the user's loop).");
  return L.join("\n");
})()
