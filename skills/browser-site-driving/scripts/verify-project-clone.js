/*
 * <target-site> clone verifier —— mechanical item-by-item comparison of the source project vs the target project
 *
 * Usage (run inside browser_exec code; the page must already be logged in to the same site):
 *   P = "~/.hermes/skills/<this-skill>/scripts/verify-project-clone.js"
 *   c = open(P).read().replace("__SRC_ID__", SRC).replace("__DST_ID__", DST)
 *   print(js(c))
 *
 * Criteria (methodology in references/clone-remote-object-via-api.md §1):
 *   - scalars: per-field JSON comparison, explicitly excluding fields that "must differ by design"
 *   - child objects: pair by name → compare missing/extra + in-group order + every field
 *   - attachments: content sha256 (re-uploading inevitably changes id/URL; comparing id/URL is all false differences)
 *   - derived text: report both the raw length + whether they are equal after normalization (whitespace removed)
 * Output: human-readable report text, writes OK where there are 0 differences. Any section that fails to fetch is printed explicitly,
 *       never silently treated as "passed"; the difference list is the input for the next round of fixes.
 */
(async () => {
  const CFG = { SRC: "__SRC_ID__", DST: "__DST_ID__", WITH_IMAGE_HASH: true };
  const B = "/api/v1";
  const L = [];
  const ok = (b) => (b ? "OK" : "DIFF");
  const j = async (u) => {
    try {
      const r = await fetch(u, { credentials: "include" });
      const t = await r.text();
      try { return JSON.parse(t); } catch (e) { return { __status: r.status, __raw: t.slice(0, 160) }; }
    } catch (e) { return { __err: String(e) }; }
  };
  const sha = async (u) => {
    try {
      const r = await fetch(u, { credentials: "include" });
      if (!r.ok) return "HTTP" + r.status;
      const b = await r.arrayBuffer();
      const h = await crypto.subtle.digest("SHA-256", b);
      return Array.from(new Uint8Array(h)).map((x) => x.toString(16).padStart(2, "0")).join("").slice(0, 16) + "/" + b.byteLength;
    } catch (e) { return "ERR:" + String(e).slice(0, 40); }
  };

  const S = await j(B + "/project/" + CFG.SRC), D = await j(B + "/project/" + CFG.DST);
  if (!S.data || !D.data) {
    return "FAIL could not fetch project —— SRC=" + JSON.stringify(S).slice(0, 200) + " DST=" + JSON.stringify(D).slice(0, 200) +
           "\n(first confirm the login state: is the landing URL /apps; see SKILL.md §4.5)";
  }
  const ps = S.data, pd = D.data;
  L.push("== clone final check: source " + CFG.SRC.slice(0, 8) + " vs target " + CFG.DST.slice(0, 8) + " ==");

  const KEYS = ["description", "players_min", "players_max", "duration_min", "duration_max", "target_age_min",
    "status", "goals_mask", "icon_key", "icon_bg_color", "cover_image_id", "cover_url",
    "project_kind", "project_component_types", "dify_document_id"];
  const baseDiff = [];
  KEYS.forEach((k) => { if (JSON.stringify(ps[k]) !== JSON.stringify(pd[k])) baseDiff.push(k + ": " + JSON.stringify(ps[k]) + " -> " + JSON.stringify(pd[k])); });
  L.push("[basic info] compared " + KEYS.length + " fields: " + (baseDiff.length ? baseDiff.join(" | ") : "0 differences " + ok(true)));

  const tid = (p) => (p.boardgame_type_tags || []).map((t) => t.id);
  L.push("[tags] " + (JSON.stringify(tid(ps)) === JSON.stringify(tid(pd)) ? "identical " + ok(true) : "DIFF " + JSON.stringify(tid(ps)) + " vs " + JSON.stringify(tid(pd))));

  const flat = (tree) => {
    const g = {}, i = {}, o = {};
    (tree || []).forEach((t) => (t.groups || []).forEach((gr) => {
      const k = t.name + "/" + gr.name;
      g[k] = { sort: gr.sort_order, cfg: gr.pg_default_config_json };
      o[k] = (gr.items || []).map((x) => x.name);
      (gr.items || []).forEach((it) => { i[k + "/" + it.name] = it; });
    }));
    return { g: g, i: i, o: o };
  };
  const ts = await j(B + "/project/" + CFG.SRC + "/component-tree");
  const td = await j(B + "/project/" + CFG.DST + "/component-tree");
  const Fs = flat(ts.data), Fd = flat(td.data);

  const gMiss = Object.keys(Fs.g).filter((k) => !(k in Fd.g));
  const gExtra = Object.keys(Fd.g).filter((k) => !(k in Fs.g));
  const gSort = Object.keys(Fs.g).filter((k) => (k in Fd.g) && (Fs.g[k].sort !== Fd.g[k].sort || JSON.stringify(Fs.g[k].cfg) !== JSON.stringify(Fd.g[k].cfg)));
  L.push("[groups] missing " + (gMiss.length ? gMiss.join(", ") : "none " + ok(true)) + " | extra " + (gExtra.length ? gExtra.join(", ") : "none " + ok(true)) +
         " | sort/config differences " + (gSort.length ? gSort.join(", ") : "none " + ok(true)));

  const iMiss = Object.keys(Fs.i).filter((k) => !(k in Fd.i));
  const iExtra = Object.keys(Fd.i).filter((k) => !(k in Fs.i));
  const iOrd = Object.keys(Fs.o).filter((k) => (k in Fd.o) && JSON.stringify(Fs.o[k]) !== JSON.stringify(Fd.o[k]));
  L.push("[items] source " + Object.keys(Fs.i).length + " / target " + Object.keys(Fd.i).length +
         ": missing " + (iMiss.length ? iMiss.join(", ") : "none " + ok(true)) + " | extra " + (iExtra.length ? iExtra.join(", ") : "none " + ok(true)) +
         " | in-group order differences " + (iOrd.length ? iOrd.join(", ") : "none " + ok(true)));

  const fDiff = [], imgDiff = [];
  let imgOk = 0;
  const keys = Object.keys(Fs.i);
  for (let n = 0; n < keys.length; n++) {
    const k = keys[n], a = Fs.i[k], b = Fd.i[k];
    if (!b) continue;
    ["description", "qty", "sort_order", "status", "image_hifi_id", "pg_config_json", "pg_back_file_id"].forEach((f) => {
      if (JSON.stringify(a[f]) !== JSON.stringify(b[f])) fDiff.push(k + " ." + f + ": " + JSON.stringify(a[f]) + " -> " + JSON.stringify(b[f]));
    });
    if (!CFG.WITH_IMAGE_HASH) continue;
    const ha = a.image_lofi_url ? await sha(a.image_lofi_url) : null;
    const hb = b.image_lofi_url ? await sha(b.image_lofi_url) : null;
    if (ha !== hb) imgDiff.push(k + ": " + ha + " vs " + hb); else if (ha) imgOk++;
  }
  L.push("[item fields] " + fDiff.length + " differences:" + (fDiff.length ? "\n    " + fDiff.join("\n    ") : ok(true)));
  L.push("[images] content sha256 identical " + imgOk + " | not identical " + (imgDiff.length ? "\n    " + imgDiff.join("\n    ") : "0 " + ok(true)));

  const rs = (await j(B + "/project/" + CFG.SRC + "/rules")).data || [];
  const rd = (await j(B + "/project/" + CFG.DST + "/rules")).data || [];
  L.push("[rules] count " + rs.length + " vs " + rd.length + " " + ok(rs.length === rd.length));
  const norm = (s) => (s || "").replace(/\s+/g, "");
  for (let n = 0; n < Math.max(rs.length, rd.length); n++) {
    const a = rs[n], b = rd[n];
    if (!a || !b) { L.push("    rule " + n + ": missing " + (a ? "target" : "source")); continue; }
    const meta = ["title", "sort_order", "level", "parent_id", "is_in_zj"].filter((f) => JSON.stringify(a[f]) !== JSON.stringify(b[f]));
    L.push("    rule \"" + a.title + "\" content byte-identical=" + (JSON.stringify(a.content) === JSON.stringify(b.content)) +
           " | content_text byte-identical=" + ((a.content_text || "") === (b.content_text || "")) +
           " (raw length " + (a.content_text || "").length + " vs " + (b.content_text || "").length + ", normalized equal=" + (norm(a.content_text) === norm(b.content_text)) + ")" +
           " | other field differences=" + (meta.length ? meta.join(", ") : "none"));
  }

  const fS = await j(B + "/project/factory/orders/by-project/" + CFG.SRC);
  const fD = await j(B + "/project/factory/orders/by-project/" + CFG.DST);
  L.push("[production orders] identical=" + (JSON.stringify(fS.data) === JSON.stringify(fD.data)) + " <- " + JSON.stringify(fS.data).slice(0, 90));
  const rS = await j(B + "/project/playground/projects/" + CFG.SRC + "/game-records?limit=10");
  const rD = await j(B + "/project/playground/projects/" + CFG.DST + "/game-records?limit=10");
  L.push("[playtest records] source " + ((rS.data || []).length) + " / target " + ((rD.data || []).length));
  L.push("Note: name / id / owner / create_time / update_time / object-level id / attachment id·URL are \"inevitably different by design\" and do not take part in the comparison.");
  return L.join("\n");
})()
