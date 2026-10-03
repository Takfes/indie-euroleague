"""Render `docs/data-map.html`: an interactive, read-only lineage map in one self-contained file.

The page draws the same graph as `docs/data-graph.md`: nodes, ids, classes and edges come from
`render_graph.build_model` (source stadium, script rectangle, dataset cylinder; one hue per stage; final =
star + thick border; in-place self-loops). This module only adds per-node detail (paths, docstring paragraph,
`Notes`, pipelines) and serialises everything as one JSON payload; the layout and interaction are ~250 lines of
dependency-free vanilla JS + SVG in `_PAGE` (no CDN, no library, works from `file://`).

The committed page is static registry content only -- no timestamps, mtimes, row counts, DVC status or absolute
paths -- and the JSON is key-sorted, so reruns are byte-identical on any machine. `status` (optional, from
`python -m eupy.registry html --status`) adds a per-script fresh/stale mark for a local, git-ignored variant.
"""

from __future__ import annotations

import ast
import json
import re
from collections.abc import Mapping
from pathlib import Path

from eupy.registry.dvc_gen import Pipeline
from eupy.registry.model import Registry
from eupy.registry.render_graph import PALETTE, GraphModel, build_model

PAYLOAD_MARKER = "__PAYLOAD__"


def parse_dvc_status(raw: str, steps: list[str]) -> dict[str, str]:
    """Map every DVC step name in `steps` to `"stale"` (listed by `dvc status --json`) or `"fresh"`.

    `raw` is the JSON text of `dvc status --json`: `{}` when everything is up to date, otherwise an object
    keyed by step name (`dvc.yaml` step names equal script names). Raises `ValueError` on anything else.
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"dvc status output is not JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("dvc status output is not a JSON object")  # noqa: TRY004
    stale = {name.split(":")[-1] for name in data}  # tolerate `path:step` keys
    return {step: ("stale" if step in stale else "fresh") for step in steps}


def _style(css: str) -> dict[str, object]:
    """`fill:#aaa,stroke:#bbb,color:#ccc,stroke-width:4px` -> `{"fill":..., "stroke":..., "text":..., "thick": bool}`."""
    parts = dict(item.split(":", 1) for item in css.split(","))
    return {
        "fill": parts["fill"],
        "stroke": parts["stroke"],
        "text": parts["color"],
        "thick": "stroke-width" in parts,
    }


def _first_paragraph(root: Path, script_path: str) -> str | None:
    """First paragraph of a script's module docstring (read with `ast`, never imported); `None` if absent."""
    try:
        tree = ast.parse((root / script_path).read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return None
    doc = ast.get_docstring(tree)
    if not doc:
        return None
    return re.split(r"\n\s*\n", doc.strip(), maxsplit=1)[0].replace("\n", " ")


def build_payload(
    reg: Registry,
    pipelines: Mapping[str, Pipeline],
    wrappers: Mapping[str, tuple[str, ...]],
    root: Path,
    status: Mapping[str, str] | None = None,
    model: GraphModel | None = None,
) -> dict[str, object]:
    """The JSON-serialisable page model: nodes (with panel details), edges, classes, pipelines, wrappers."""
    model = model or build_model(reg)
    ids = model.ids
    member_of: dict[str, list[str]] = {}
    for pname, pipe in pipelines.items():
        for script in pipe.scripts:
            member_of.setdefault(script, []).append(pname)

    nodes: dict[str, dict[str, object]] = {}
    for label, src in reg.sources.items():
        nodes[ids[f"source:{label}"]] = {
            "kind": "source",
            "label": label,
            "stage": None,
            "final": False,
            "detail": {
                "scripts": [ids[f"script:{s}"] for s in src.scripts],
                "datasets": [ids[f"dataset:{d}"] for d in src.datasets],
            },
        }
    for name, script in reg.scripts.items():
        nodes[ids[f"script:{name}"]] = {
            "kind": "script",
            "label": name,
            "stage": max((reg.stage(o) for o in script.outputs), default=0),
            "final": script.final,
            "detail": {
                "path": script.path,
                "inputs": [ids[f"dataset:{d}"] for d in script.inputs],
                "outputs": [ids[f"dataset:{d}"] for d in script.outputs],
                "sources": [ids[f"source:{s}"] for s in script.sources if f"source:{s}" in ids],
                "in_place": [ids[f"dataset:{d}"] for d in script.in_place],
                "final": script.final,
                "impure": script.impure,
                "pipelines": sorted(member_of.get(name, [])),
                "refresh": script.refresh,
                "doc": _first_paragraph(root, script.path),
                "notes": script.notes,
                **({"status": status[name]} if status and name in status else {}),
            },
        }
    for name, ds in reg.datasets.items():
        nodes[ids[f"dataset:{name}"]] = {
            "kind": "dataset",
            "label": name,
            "stage": ds.stage,
            "final": ds.final,
            "detail": {
                "path": ds.path,
                "stage": ds.stage,
                "raw": ds.raw,
                "producer": ids[f"script:{ds.producer}"] if ds.producer else None,
                "origin": ds.origin,
                "updaters": [ids[f"script:{s}"] for s in ds.updaters],
                "consumers": [ids[f"script:{s}"] for s in ds.consumers],
                "final": ds.final,
                "refresh": ds.refresh,
            },
        }
    for node, (_, cls) in model.decl.items():
        nodes[ids[node]]["cls"] = cls

    return {
        "nodes": nodes,
        "edges": [[ids[a], ids[b]] for a, b in model.edges],
        "classes": {name: _style(css) for name, css in model.classes.items()},
        "palette": [{"fill": f, "stroke": s, "text": t} for f, s, t in PALETTE],
        "pipelines": {p: [ids[f"script:{s}"] for s in sorted(pipe.scripts)] for p, pipe in sorted(pipelines.items())},
        "wrappers": {w: list(members) for w, members in sorted(wrappers.items())},
        "status": bool(status),
    }


def render_html(
    reg: Registry,
    pipelines: Mapping[str, Pipeline],
    wrappers: Mapping[str, tuple[str, ...]],
    root: Path,
    status: Mapping[str, str] | None = None,
) -> str:
    """Full text of the page; raises `GraphError` when the registry cannot be drawn."""
    payload = build_payload(reg, pipelines, wrappers, root, status)
    # ensure_ascii + escaping `<`/`>`/`&` keeps header text from ever closing the <script> element.
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    blob = blob.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return _PAGE.replace(PAYLOAD_MARKER, blob)


_PAGE = r"""<!doctype html>
<!-- GENERATED — do not edit. Regenerate with `uv run python -m eupy.registry html`. -->
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>EuroLeague Fantasy — data map</title>
<style>
:root { --bg:#fafafa; --panel:#fff; --fg:#222; --muted:#666; --line:#c9c9c9; --edge:#8a8a8a; --hl:#1a73e8; --border:#ddd; --stale:#d93025; --fresh:#188038; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#16181c; --panel:#20232a; --fg:#e6e6e6; --muted:#9aa0a6; --line:#3a3f47; --edge:#7d838c; --hl:#8ab4f8; --border:#3a3f47; --stale:#f28b82; --fresh:#81c995; }
}
* { box-sizing:border-box; }
html, body { height:100%; margin:0; }
body { display:flex; flex-direction:column; background:var(--bg); color:var(--fg); font:13px/1.45 system-ui, sans-serif; }
header { display:flex; flex-wrap:wrap; gap:12px; align-items:center; padding:8px 12px; border-bottom:1px solid var(--border); background:var(--panel); }
header h1 { font-size:15px; margin:0; }
select, button { font:inherit; color:var(--fg); background:var(--bg); border:1px solid var(--border); border-radius:4px; padding:2px 6px; }
button.link { border:0; background:none; color:var(--hl); padding:0; cursor:pointer; text-align:left; }
main { flex:1; display:flex; min-height:0; }
#stage { flex:1; overflow:auto; }
#panel { width:340px; overflow:auto; padding:12px; border-left:1px solid var(--border); background:var(--panel); }
#panel h2 { font-size:14px; margin:0 0 2px; word-break:break-all; }
#panel dt { color:var(--muted); margin-top:8px; font-size:11px; text-transform:uppercase; letter-spacing:.04em; }
#panel dd { margin:0; word-break:break-word; }
#panel ul { margin:0; padding-left:16px; }
.muted { color:var(--muted); }
#legend { display:flex; flex-wrap:wrap; gap:10px; align-items:center; margin-left:auto; color:var(--muted); }
#legend span.sw { display:inline-block; width:12px; height:12px; border-radius:2px; vertical-align:-2px; margin-right:3px; border:1px solid; }
svg text { font:12px system-ui, sans-serif; pointer-events:none; }
.node { cursor:pointer; }
.node.dim, .edge.dim { opacity:.13; }
.node.sel .shape { stroke:var(--hl) !important; stroke-width:3px !important; }
.edge { fill:none; stroke:var(--edge); stroke-width:1.2; }
.edge.on { stroke:var(--hl); stroke-width:2; }
.mark { stroke:none; }
</style>
</head>
<body>
<header>
  <h1>EuroLeague Fantasy — data map</h1>
  <label>Pipeline <select id="filter"></select></label>
  <span id="statusnote" class="muted"></span>
  <div id="legend"></div>
</header>
<main>
  <div id="stage"><svg id="svg" xmlns="http://www.w3.org/2000/svg"></svg></div>
  <aside id="panel"><p class="muted">Click a node for details. Pick a pipeline or wrapper to highlight its scripts and the data they touch.</p></aside>
</main>
<script id="payload" type="application/json">__PAYLOAD__</script>
<script>
(function () {
"use strict";
var D = JSON.parse(document.getElementById("payload").textContent);
var NS = "http://www.w3.org/2000/svg";
var ids = Object.keys(D.nodes).sort();
var W = {}, H = {}, POS = {};

// ---- small DOM helpers (text only ever goes through textContent) ----
function el(tag, attrs, text, ns) {
  var n = ns ? document.createElementNS(NS, tag) : document.createElement(tag);
  for (var k in attrs || {}) n.setAttribute(k, attrs[k]);
  if (text != null) n.textContent = text;
  return n;
}
function svg(tag, attrs, text) { return el(tag, attrs, text, true); }
function labelOf(n) { return n.kind === "dataset" ? n.label + (n.final ? " ★" : "") : n.label; }
function shortOf(n) { var t = labelOf(n); return t.length > 42 ? t.slice(0, 41) + "…" : t; } // full text: tooltip + panel

// ---- layout: longest-path layers (in-place loops excluded), barycenter ordering, columns ----
var inPlace = {}; // "a>b" edges that close an in-place loop (script -> its own input dataset)
ids.forEach(function (id) {
  var n = D.nodes[id];
  if (n.kind === "script") n.detail.in_place.forEach(function (d) { inPlace[id + ">" + d] = true; });
});
var fwd = D.edges.filter(function (e) { return !inPlace[e[0] + ">" + e[1]]; });
var layer = {}, preds = {}, succs = {};
ids.forEach(function (id) { preds[id] = []; succs[id] = []; });
fwd.forEach(function (e) { preds[e[1]].push(e[0]); succs[e[0]].push(e[1]); });
function depth(id) {
  if (layer[id] != null) return layer[id];
  layer[id] = 0; // guards against an unexpected cycle
  var d = 0;
  preds[id].forEach(function (p) { d = Math.max(d, depth(p) + 1); });
  return (layer[id] = d);
}
ids.forEach(depth);
var cols = [];
ids.forEach(function (id) { (cols[layer[id]] = cols[layer[id]] || []).push(id); });
var order = {};
cols.forEach(function (c) { c.forEach(function (id, i) { order[id] = i; }); });
function sweep(neigh, list) {
  var bc = {};
  list.forEach(function (id) {
    var ns = neigh[id];
    bc[id] = ns.length ? ns.reduce(function (s, x) { return s + order[x]; }, 0) / ns.length : order[id];
  });
  list.sort(function (a, b) { return bc[a] - bc[b] || (a < b ? -1 : 1); });
  list.forEach(function (id, i) { order[id] = i; });
}
for (var it = 0; it < 6; it++) {
  for (var i = 1; i < cols.length; i++) sweep(preds, cols[i]);
  for (var j = cols.length - 2; j >= 0; j--) sweep(succs, cols[j]);
}
var probe = svg("text", {}); document.getElementById("svg").appendChild(probe);
ids.forEach(function (id) {
  var n = D.nodes[id];
  probe.textContent = shortOf(n);
  var lw = probe.getComputedTextLength() || shortOf(n).length * 6.5;
  W[id] = Math.ceil(lw + (n.kind === "source" ? 40 : 28));
  H[id] = n.kind === "dataset" ? 44 : 32;
});
document.getElementById("svg").removeChild(probe);
var GAPX = 80, GAPY = 16, PAD = 24, x = PAD, colH = [], totalH = 0;
cols.forEach(function (c, i) {
  var w = Math.max.apply(null, c.map(function (id) { return W[id]; }));
  var h = c.reduce(function (s, id) { return s + H[id] + GAPY; }, -GAPY);
  colH[i] = h; totalH = Math.max(totalH, h);
  c.forEach(function (id) { POS[id] = { cx: x + w / 2 }; });
  cols[i].w = w; x += w + GAPX;
});
cols.forEach(function (c, i) {
  var y = PAD + (totalH - colH[i]) / 2;
  c.forEach(function (id) { POS[id].cy = y + H[id] / 2; y += H[id] + GAPY; });
});
var totalW = x - GAPX + PAD, totalHt = totalH + 2 * PAD + 40;

// ---- draw ----
var root = document.getElementById("svg");
root.setAttribute("width", totalW); root.setAttribute("height", totalHt);
root.setAttribute("viewBox", "0 0 " + totalW + " " + totalHt);
var defs = svg("defs"); root.appendChild(defs);
[["arr", "var(--edge)"], ["arrOn", "var(--hl)"]].forEach(function (m) {
  var mk = svg("marker", { id: m[0], viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: "auto-start-reverse" });
  mk.appendChild(svg("path", { d: "M0 0L10 5L0 10z", fill: m[1] })); defs.appendChild(mk);
});
var edgeG = svg("g"), nodeG = svg("g"); root.appendChild(edgeG); root.appendChild(nodeG);
var edgeEls = D.edges.map(function (e) {
  var a = e[0], b = e[1], pa = POS[a], pb = POS[b], d;
  if (layer[b] > layer[a]) {
    var x1 = pa.cx + W[a] / 2, x2 = pb.cx - W[b] / 2, mx = (x1 + x2) / 2;
    d = "M" + x1 + " " + pa.cy + "C" + mx + " " + pa.cy + " " + mx + " " + pb.cy + " " + x2 + " " + pb.cy;
  } else { // back edge (in-place loop): arc under both nodes
    var y1 = Math.max(pa.cy + H[a] / 2, pb.cy + H[b] / 2) + 26;
    d = "M" + pa.cx + " " + (pa.cy + H[a] / 2) + "C" + pa.cx + " " + y1 + " " + pb.cx + " " + y1 + " " + pb.cx + " " + (pb.cy + H[b] / 2);
  }
  var p = svg("path", { "class": "edge", d: d, "marker-end": "url(#arr)" });
  edgeG.appendChild(p); return p;
});
var nodeEls = {};
ids.forEach(function (id) {
  var n = D.nodes[id], c = D.classes[n.cls], p = POS[id], w = W[id], h = H[id], sh;
  var g = svg("g", { "class": "node", transform: "translate(" + p.cx + "," + p.cy + ")", tabindex: 0, role: "button" });
  var st = { fill: c.fill, stroke: c.stroke, "stroke-width": c.thick ? 4 : 1.5, "class": "shape" };
  if (n.kind === "source") { st.x = -w / 2; st.y = -h / 2; st.width = w; st.height = h; st.rx = h / 2; sh = svg("rect", st); }
  else if (n.kind === "script") { st.x = -w / 2; st.y = -h / 2; st.width = w; st.height = h; st.rx = 2; sh = svg("rect", st); }
  else { // cylinder
    var r = 7, l = -w / 2, rt = w / 2, t = -h / 2, bt = h / 2;
    st.d = "M" + l + " " + (t + r) + "A" + w / 2 + " " + r + " 0 0 1 " + rt + " " + (t + r) + "V" + (bt - r) +
      "A" + w / 2 + " " + r + " 0 0 1 " + l + " " + (bt - r) + "Z";
    sh = svg("path", st);
  }
  g.appendChild(sh);
  if (n.kind === "dataset") {
    g.appendChild(svg("path", { d: "M" + (-w / 2) + " " + (-h / 2 + 7) + "A" + w / 2 + " 7 0 0 0 " + w / 2 + " " + (-h / 2 + 7), fill: "none", stroke: c.stroke, "stroke-width": 1 }));
    g.appendChild(svg("text", { y: 1, "text-anchor": "middle", fill: c.text }, shortOf(n)));
    g.appendChild(svg("text", { y: 15, "text-anchor": "middle", fill: c.text, opacity: 0.7 }, "stage " + n.stage));
  } else {
    g.appendChild(svg("text", { y: 4, "text-anchor": "middle", fill: c.text }, shortOf(n)));
  }
  g.appendChild(svg("title", {}, labelOf(n)));
  if (D.status && n.kind === "script" && n.detail.status) {
    g.appendChild(svg("circle", { cx: w / 2 - 2, cy: -h / 2 + 2, r: 5, "class": "mark", fill: n.detail.status === "stale" ? "var(--stale)" : "var(--fresh)" }));
  }
  g.addEventListener("click", function (ev) { ev.stopPropagation(); select(id); });
  g.addEventListener("keydown", function (ev) { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); select(id); } });
  nodeG.appendChild(g); nodeEls[id] = g;
});

// ---- legend ----
var lg = document.getElementById("legend");
function chip(text, fill, stroke) {
  var s = el("span"); var sw = el("span", { "class": "sw" }); sw.style.background = fill; sw.style.borderColor = stroke;
  s.appendChild(sw); s.appendChild(document.createTextNode(text)); lg.appendChild(s);
}
lg.appendChild(el("span", {}, "stadium = source · rectangle = script · cylinder = dataset · ★ thick border = final · loop under nodes = in-place rewrite"));
var maxStage = 0; ids.forEach(function (id) { if (D.nodes[id].stage != null) maxStage = Math.max(maxStage, D.nodes[id].stage); });
for (var s = 0; s <= maxStage; s++) chip("stage " + s, D.palette[s].fill, D.palette[s].stroke);
chip("source", D.classes.source.fill, D.classes.source.stroke);
if (D.status) lg.appendChild(el("span", {}, "● green = DVC step fresh, red = stale"));

// ---- pipeline filter: dim everything except the scripts and the data/sources touching them ----
var sel = document.getElementById("filter");
sel.appendChild(el("option", { value: "" }, "(all)"));
var grp1 = el("optgroup", { label: "wrappers" }), grp2 = el("optgroup", { label: "pipelines" });
Object.keys(D.wrappers).forEach(function (w) { grp1.appendChild(el("option", { value: "w:" + w }, w + " (" + D.wrappers[w].join(" + ") + ")")); });
Object.keys(D.pipelines).forEach(function (p) { grp2.appendChild(el("option", { value: "p:" + p }, p)); });
if (grp1.children.length) sel.appendChild(grp1);
sel.appendChild(grp2);
var selected = null, kept = null;
function pipelineScripts(v) {
  if (!v) return null;
  var names = v.charAt(0) === "w" ? D.wrappers[v.slice(2)] : [v.slice(2)], out = {};
  names.forEach(function (p) { D.pipelines[p].forEach(function (s) { out[s] = true; }); });
  return out;
}
function refresh() {
  var scripts = pipelineScripts(sel.value);
  kept = null;
  if (scripts) {
    kept = {};
    Object.keys(scripts).forEach(function (s) { kept[s] = true; });
    D.edges.forEach(function (e) { if (scripts[e[0]]) kept[e[1]] = true; if (scripts[e[1]]) kept[e[0]] = true; });
  }
  ids.forEach(function (id) {
    nodeEls[id].classList.toggle("dim", !!kept && !kept[id]);
    nodeEls[id].classList.toggle("sel", id === selected);
  });
  D.edges.forEach(function (e, i) {
    var on = selected && (e[0] === selected || e[1] === selected);
    var p = edgeEls[i];
    p.classList.toggle("dim", !!kept && !(kept[e[0]] && kept[e[1]]));
    p.classList.toggle("on", !!on);
    p.setAttribute("marker-end", on ? "url(#arrOn)" : "url(#arr)");
  });
}
sel.addEventListener("change", function () { refresh(); });
root.addEventListener("click", function () { selected = null; renderPanel(null); refresh(); });

// ---- side panel ----
var panel = document.getElementById("panel");
function row(dl, key, value) {
  if (value == null || value === "" || (Array.isArray(value) && !value.length)) return;
  dl.appendChild(el("dt", {}, key));
  var dd = el("dd");
  if (Array.isArray(value)) {
    var ul = el("ul");
    value.forEach(function (v) { var li = el("li"); li.appendChild(ref(v)); ul.appendChild(li); });
    dd.appendChild(ul);
  } else if (typeof value === "object") { dd.appendChild(ref(value.id)); }
  else dd.textContent = String(value);
  dl.appendChild(dd);
}
function ref(id) { // clickable link to another node; plain text for a name that is not a node
  if (!D.nodes[id]) return document.createTextNode(String(id));
  var b = el("button", { "class": "link", type: "button" }, D.nodes[id].label);
  b.addEventListener("click", function () { select(id); });
  return b;
}
function yn(b) { return b ? "yes" : "no"; }
function renderPanel(id) {
  panel.textContent = "";
  if (!id) { panel.appendChild(el("p", { "class": "muted" }, "Click a node for details.")); return; }
  var n = D.nodes[id], d = n.detail, dl = el("dl");
  panel.appendChild(el("h2", {}, labelOf(n)));
  panel.appendChild(el("div", { "class": "muted" }, n.kind));
  if (n.kind === "script") {
    row(dl, "path", d.path); row(dl, "description", d.doc);
    row(dl, "inputs", d.inputs); row(dl, "outputs", d.outputs); row(dl, "in-place rewrite of", d.in_place);
    row(dl, "external sources", d.sources);
    row(dl, "final", yn(d.final)); row(dl, "impure", yn(d.impure));
    row(dl, "pipeline", d.pipelines.join(", ")); row(dl, "refresh", d.refresh); row(dl, "notes", d.notes);
    row(dl, "dvc status", d.status);
  } else if (n.kind === "dataset") {
    row(dl, "path", d.path); row(dl, "stage", d.stage);
    row(dl, "producer", d.producer ? { id: d.producer } : (d.raw ? "none (raw" + (d.origin ? ": " + d.origin : "") + ")" : null));
    row(dl, "updaters", d.updaters); row(dl, "consumers", d.consumers);
    row(dl, "final", yn(d.final)); row(dl, "refresh", d.refresh);
  } else {
    row(dl, "feeds scripts", d.scripts); row(dl, "feeds datasets", d.datasets);
  }
  panel.appendChild(dl);
}
function select(id) { selected = id; renderPanel(id); refresh(); }

if (D.status) document.getElementById("statusnote").textContent = "local DVC status overlay";
renderPanel(null); refresh();
})();
</script>
</body>
</html>
"""
