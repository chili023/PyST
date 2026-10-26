"""
Laeufe als eigenstaendige HTML-Datei zum Ansehen ohne PyST (Kunde zu Hause, Handy oder PC, offline).

Bewusst einfach: oben die Hauptwerte gross (Pmax, Mmax, Fahrzeug, Datum, Klima), ein Diagramm
Leistung + Drehmoment ueber Drehzahl, darunter hoechstens zwei zuschaltbare Kanaele, Werte unter dem
Cursor, Zoom (Bereich ziehen) und „Zoom zurück“. Mehrere Laeufe: Auswahl + optional ein Vergleichslauf.

Eine Datei: Plot-Bibliothek uPlot 1.6.31 (MIT-Lizenz, (c) Leon Sorokin, Lizenztext steht in der Datei)
und die Daten sind eingebettet, kein Internet noetig.
"""
import datetime as _dt
import html
import json
import os
from typing import Any, Dict, List, Optional

import numpy as np

from . import __version__, physics

UPLOT_DIR = os.path.join(os.path.dirname(__file__), "data", "uplot")
MAX_POINTS = 3000          # je Lauf und Kanal (Dauermessungen werden ausgeduennt)
PS_TO_KW = 0.73549875

# Kanaele, die zugeschaltet werden koennen (Name im Auswerter -> Anzeige), sofern im Lauf vorhanden
EXTRA_CHANNELS = ["Lambda", "AFR", "EGT 1", "EGT 2", "Drehzahl Zündung", "Geschwindigkeit",
                  "Übersetzung gefiltert", "Drehzahl Rolle", "Fehler Rolle", "Fehler Zündung"]


def _clean(a) -> List[Optional[float]]:
    a = np.asarray(a, float)
    return [None if not np.isfinite(x) else round(float(x), 4) for x in a]


def _thin(n: int) -> np.ndarray:
    if n <= MAX_POINTS:
        return np.arange(n)
    return np.linspace(0, n - 1, MAX_POINTS).astype(int)


def run_payload(label: str, run: Dict[str, Any], res, ch: Dict[str, Any], p: physics.DynoParams,
                vehicle: str = "", setup: str = "", notiz: str = "", datum: str = "",
                fehler: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    """Daten eines Laufs fuer die HTML-Datei (Kurve ueber Drehzahl bzw. bei Dauermessungen ueber der Zeit)."""
    out: Dict[str, Any] = {"label": label, "fahrzeug": vehicle, "setup": setup, "notiz": notiz,
                           "datum": datum, "kenn": {}, "fehler": {k: round(v, 1) for k, v in (fehler or {}).items()}}
    out["kenn"]["klima"] = (f"{p.temp_c:.1f} °C · {p.p_mbar:.0f} mbar · k = {physics.din70020(p.temp_c, p.p_mbar):.3f}"
                            .replace(".", ",") + " (DIN 70020)")
    if res is not None and res.full_ps is not None and len(res.n):
        seg = res.segment
        idx = _thin(len(res.n))
        out["x"] = {"name": "Drehzahl", "unit": "1/min", "v": _clean(np.asarray(res.n)[idx])}
        out["main"] = True
        out["ps"] = _clean(np.asarray(res.ps)[idx])
        out["nm"] = _clean(np.asarray(res.nm)[idx])
        out["kenn"].update(pmax=round(res.p_max, 1), kw=round(res.p_max * PS_TO_KW, 1), n_pmax=round(res.n_pmax),
                           mmax=round(res.m_max, 1), n_mmax=round(res.n_mmax), vmax=round(res.v_max, 1))
        sel = lambda c: np.asarray(c.values)[seg][idx] if len(np.asarray(c.values)[seg]) == len(res.n) else None
    else:
        t = np.asarray(ch["Zeit"].values)
        idx = _thin(len(t))
        out["x"] = {"name": "Zeit", "unit": "s", "v": _clean(t[idx] - (t[0] if len(t) else 0))}
        out["main"] = False
        out["ps"] = _clean(np.asarray(ch["Drehzahl Motor (roh)"].values)[idx])        # Ersatz: Drehzahlen
        out["nm"] = _clean(np.asarray(ch["Drehzahl Zündung"].values)[idx])
        sel = lambda c: np.asarray(c.values)[idx] if len(c.values) == len(t) else None
    extra = {}
    for name in EXTRA_CHANNELS:
        c = ch.get(name)
        if c is None:
            continue
        v = sel(c)
        if v is None or not np.isfinite(np.asarray(v, float)).any():
            continue
        extra[name] = {"unit": c.unit, "v": _clean(v)}
    out["extra"] = extra
    return out


def export_html(path: str, runs: List[Dict[str, Any]], title: str = "Leistungsmessung") -> int:
    """runs: Liste von run_payload(). Liefert die Dateigroesse in Bytes."""
    with open(os.path.join(UPLOT_DIR, "uPlot.iife.min.js"), encoding="utf-8") as fh:
        js = fh.read()
    with open(os.path.join(UPLOT_DIR, "uPlot.min.css"), encoding="utf-8") as fh:
        css = fh.read()
    with open(os.path.join(UPLOT_DIR, "LICENSE"), encoding="utf-8") as fh:
        lic = fh.read().replace("*/", "* /")
    data = json.dumps({"titel": title, "laeufe": runs,
                       "erstellt": _dt.datetime.now().strftime("%d.%m.%Y %H:%M"),
                       "programm": f"PyST {__version__}"}, ensure_ascii=False, separators=(",", ":"))
    data = data.replace("</", "<\\/")                       # kein vorzeitiges </script>
    page = (TEMPLATE.replace("%%TITLE%%", html.escape(title)).replace("%%UPLOT_CSS%%", css)
            .replace("%%UPLOT_LICENSE%%", lic).replace("%%UPLOT_JS%%", js).replace("%%DATA%%", data))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(page)
    return os.path.getsize(path)


TEMPLATE = r"""<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>%%TITLE%%</title>
<style>%%UPLOT_CSS%%</style>
<style>
:root { --bg:#ffffff; --fg:#1d1d1f; --muted:#6b6b70; --card:#f4f4f6; --line:#d9d9de;
        --ps:#c62828; --nm:#1f5fbf; --x1:#2e7d32; --x2:#ef6c00; --cmp:#7a7a80; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#16171a; --fg:#ececf0; --muted:#9a9aa3; --card:#23242a; --line:#3a3b42;
          --ps:#ff6b6b; --nm:#6aa5ff; --x1:#6cc070; --x2:#ffaa55; --cmp:#a0a0a8; }
}
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--fg);
       font: 15px/1.4 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
main { max-width: 1100px; margin: 0 auto; padding: 12px 16px 32px; }
h1 { font-size: 20px; margin: 4px 0 2px; }
.sub { color: var(--muted); font-size: 13px; margin-bottom: 10px; }
.bar { display:flex; flex-wrap:wrap; gap:8px 14px; align-items:center; margin: 8px 0; }
.bar label { color: var(--muted); font-size: 13px; }
select, button { font: inherit; color: var(--fg); background: var(--card); border: 1px solid var(--line);
                 border-radius: 6px; padding: 4px 8px; max-width: 100%; }
button { cursor: pointer; }
.tiles { display:grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 8px; margin: 8px 0 12px; }
.tile { background: var(--card); border-radius: 8px; padding: 8px 12px; }
.tile .k { color: var(--muted); font-size: 12px; }
.tile .v { font-size: 26px; font-weight: 700; }
.tile .v small { font-size: 14px; font-weight: 400; color: var(--muted); }
.tile .t { font-size: 14px; }
#plot, #plot2 { width: 100%; }
.u-legend { font-size: 13px; }
.uplot, .u-title { color: var(--fg); }
.hint, footer { color: var(--muted); font-size: 12px; margin-top: 8px; }
.cmp { color: var(--cmp); }
</style>
</head>
<body>
<main>
<h1 id="title"></h1>
<div class="sub" id="sub"></div>
<div class="bar" id="runbar">
  <span id="runsel"><label for="run">Lauf</label> <select id="run"></select></span>
  <span id="cmpsel"><label for="cmp">Vergleich</label> <select id="cmp"></select></span>
</div>
<div class="tiles" id="tiles"></div>
<div id="plot"></div>
<div class="bar">
  <label for="e1">Kanal 1</label> <select id="e1"></select>
  <label for="e2">Kanal 2</label> <select id="e2"></select>
  <button id="reset">Zoom zurück</button>
</div>
<div id="plot2"></div>
<div class="hint">Werte: Maus bzw. Finger über das Diagramm · Zoom: Bereich mit der Maus ziehen · „Zoom zurück“ = alles.</div>
<footer id="foot"></footer>
</main>
<script>/*
uPlot v1.6.31 – https://github.com/leeoniya/uPlot
%%UPLOT_LICENSE%%
*/</script>
<script>%%UPLOT_JS%%</script>
<script>
const D = %%DATA%%;
const $ = id => document.getElementById(id);
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const fmt = (v, d) => v == null ? "–" : v.toLocaleString("de-DE", {minimumFractionDigits: d, maximumFractionDigits: d});
let u1 = null, u2 = null, xRange = null;

function fill(sel, items, keep) {
  sel.innerHTML = "";
  items.forEach(([v, t]) => { const o = document.createElement("option"); o.value = v; o.textContent = t; sel.appendChild(o); });
  if (keep != null && items.some(i => String(i[0]) === String(keep))) sel.value = keep;
}

function tiles(r) {
  const k = r.kenn, out = [];
  if (r.main) {
    out.push(["Leistung max.", `${fmt(k.pmax, 1)} <small>PS · ${fmt(k.kw, 1)} kW</small>`, `bei ${fmt(k.n_pmax, 0)} 1/min`]);
    out.push(["Drehmoment max.", `${fmt(k.mmax, 1)} <small>Nm</small>`, `bei ${fmt(k.n_mmax, 0)} 1/min`]);
  }
  out.push(["Fahrzeug", "", [r.fahrzeug || "–", r.setup].filter(Boolean).join("<br>")]);
  out.push(["Datum", "", r.datum || "–"]);
  out.push(["Klima / Korrektur", "", k.klima || "–"]);
  const f = Object.entries(r.fehler || {}).filter(([n, v]) => v >= 0.1);
  if (f.length) out.push(["Messfehler (Anteil)", "", f.map(([n, v]) => `${n}: ${fmt(v, 1)} %`).join("<br>")]);
  $("tiles").innerHTML = out.map(([a, b, c]) =>
    `<div class="tile"><div class="k">${a}</div>${b ? `<div class="v">${b}</div>` : ""}<div class="t">${c}</div></div>`).join("");
  $("sub").textContent = [r.label, r.notiz].filter(Boolean).join(" · ");
}

function width() { return Math.max(280, $("plot").clientWidth); }

function draw() {
  const r = D.laeufe[+$("run").value];
  const ci = $("cmp").value === "" ? null : +$("cmp").value;
  const c = ci == null ? null : D.laeufe[ci];
  tiles(r);
  const mainNames = r.main ? ["Leistung", "Drehmoment"] : ["Motor (Rolle × i)", "Motor (Zündung)"];
  const mainUnits = r.main ? ["PS", "Nm"] : ["1/min", "1/min"];
  // Tabellen [x, a, b] je Lauf -> gemeinsame X-Werte (uPlot.join)
  const tabs = [[r.x.v, r.ps, r.nm]];
  if (c) tabs.push([c.x.v, c.ps, c.nm]);
  const sorted = t => { const o = t[0].map((_, i) => i).filter(i => t[0][i] != null).sort((a, b) => t[0][a] - t[0][b]);
                        return t.map(col => o.map(i => col[i])); };
  const data = uPlot.join(tabs.map(sorted));
  const series = [{label: `${r.x.name} [${r.x.unit}]`, value: (u, v) => fmt(v, r.x.unit === "s" ? 2 : 0)},
    {label: `${mainNames[0]} [${mainUnits[0]}]`, stroke: css("--ps"), width: 2.5, scale: "a", spanGaps: true, value: (u, v) => fmt(v, 1)},
    {label: `${mainNames[1]} [${mainUnits[1]}]`, stroke: css("--nm"), width: 2.5, scale: "b", spanGaps: true, value: (u, v) => fmt(v, 1)}];
  if (c) {
    series.push({label: `Vergleich ${mainNames[0]}`, stroke: css("--ps"), dash: [6, 5], width: 1.5, scale: "a", spanGaps: true, value: (u, v) => fmt(v, 1)});
    series.push({label: `Vergleich ${mainNames[1]}`, stroke: css("--nm"), dash: [6, 5], width: 1.5, scale: "b", spanGaps: true, value: (u, v) => fmt(v, 1)});
  }
  const axis = (scale, side, color, label) => ({scale, side, label, stroke: css("--fg"), labelFont: "12px sans-serif",
    grid: {stroke: css("--line"), width: side === 3 ? 1 : 0}, ticks: {stroke: css("--line")}, values: (u, v) => v.map(x => fmt(x, 0)),
    labelGap: 0, size: 52, _c: color});
  const common = {width: width(), cursor: {sync: {key: "pyst", setSeries: false}, drag: {x: true, y: false}},
    scales: {x: {time: false}},
    hooks: {setScale: [(u, k) => { if (k === "x" && !syncing) zoomBoth(u); }]}};
  const isNarrow = window.innerWidth < 600;
  [u1, u2].forEach(u => u && u.destroy());
  u1 = u2 = null;
  syncing = true;                                  // beim Aufbau nicht gegenseitig zoomen
  u1 = new uPlot({...common, height: isNarrow ? 300 : 420, series,
    axes: [{stroke: css("--fg"), grid: {stroke: css("--line")}, ticks: {stroke: css("--line")}, label: `${r.x.name} [${r.x.unit}]`,
            values: (u, v) => v.map(x => fmt(x, r.x.unit === "s" ? 1 : 0))},
           axis("a", 3, css("--ps"), `${mainNames[0]} [${mainUnits[0]}]`),
           axis("b", 1, css("--nm"), `${mainNames[1]} [${mainUnits[1]}]`)]}, data, $("plot"));
  // zweites Diagramm: hoechstens zwei zugeschaltete Kanaele
  const extras = [$("e1").value, $("e2").value].filter((n, i, a) => n && r.extra[n] && a.indexOf(n) === i);
  if (extras.length) {
    const tab = [r.x.v, ...extras.map(n => r.extra[n].v)];
    const s2 = [{label: series[0].label, value: series[0].value}];
    const ax2 = [{stroke: css("--fg"), grid: {stroke: css("--line")}, ticks: {stroke: css("--line")},
                  values: (u, v) => v.map(x => fmt(x, r.x.unit === "s" ? 1 : 0))}];
    extras.forEach((n, i) => {
      const col = css(i ? "--x2" : "--x1"), unit = r.extra[n].unit;
      s2.push({label: `${n}${unit ? " [" + unit + "]" : ""}`, stroke: col, width: 2, scale: "e" + i, spanGaps: true,
               value: (u, v) => fmt(v, Math.abs(v) < 10 ? 2 : 0)});
      ax2.push({scale: "e" + i, side: i ? 1 : 3, stroke: col, grid: {show: i === 0, stroke: css("--line")}, size: 52,
                values: (u, v) => v.map(x => fmt(x, Math.abs(x) < 10 ? 2 : 0))});
    });
    u2 = new uPlot({...common, height: isNarrow ? 160 : 200, series: s2, axes: ax2}, sorted(tab), $("plot2"));
  }
  if (xRange) [u1, u2].forEach(u => u && u.setScale("x", xRange));
  syncing = false;
}
let syncing = false;
function zoomBoth(src) {
  const s = src.scales.x;
  xRange = {min: s.min, max: s.max};
  syncing = true;
  [u1, u2].forEach(u => { if (u && u !== src) u.setScale("x", xRange); });
  syncing = false;
}
function resetZoom() { xRange = null; draw(); }

function channelOptions() {
  const r = D.laeufe[+$("run").value];
  const items = [["", "keiner"]].concat(Object.keys(r.extra).map(n => [n, n]));
  fill($("e1"), items, $("e1").value);
  fill($("e2"), items, $("e2").value);
}

function init() {
  document.title = D.titel;
  $("title").textContent = D.titel;
  fill($("run"), D.laeufe.map((r, i) => [i, r.label]));
  const cmpItems = [["", "kein Vergleich"]].concat(D.laeufe.map((r, i) => [i, r.label]));
  fill($("cmp"), cmpItems);
  if (D.laeufe.length < 2) { $("runbar").style.display = "none"; }
  const r0 = D.laeufe[0];
  channelOptions();
  const pref = ["Lambda", "AFR", "EGT 1"].find(n => r0.extra[n]);
  if (pref) $("e1").value = pref;
  $("foot").textContent = `Erstellt ${D.erstellt} mit ${D.programm} · Diagramme: uPlot (MIT-Lizenz)`;
  $("run").onchange = () => { xRange = null; channelOptions(); draw(); };
  $("cmp").onchange = () => { xRange = null; draw(); };
  $("e1").onchange = $("e2").onchange = () => draw();
  $("reset").onclick = resetZoom;
  draw();
  let w = width();
  new ResizeObserver(() => { const n = width(); if (Math.abs(n - w) > 4) { w = n; [u1, u2].forEach(u => u && u.setSize({width: n, height: u.height})); } }).observe($("plot"));
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", draw);
}
init();
</script>
</body>
</html>
"""
