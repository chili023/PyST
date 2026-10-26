"""
Log der Zange (per USB ausgelesen, '#LOG GET' -> 'L;…'-Zeilen) als Dauermessung ablegen und weitergeben.

Aus den Funken-Zeitstempeln (Lokalzeit der Zange, µs) entstehen Telegramme wie bei der Messelektronik
(Standard 50 Hz): Zuendfrequenz aus den Perioden, die im Fenster enden (wie PST-Diag 1 §8.5), Flanken je
Fenster, kuerzeste/laengste Periode, verworfene Funken, Zuendstaerke (Mittel der Spitzenwerte). Rolle = 0.
Marker-Tasten -> marker.csv, Status (Akku) als Zusatzspalte, das Rohlog als zange_log.csv.
Lange Pausen (Zange aus) werden nicht mit leeren Telegrammen aufgefuellt.
"""
import csv
import datetime as _dt
import json
import os
from dataclasses import asdict
from typing import Dict, List, Optional

import numpy as np

from . import __version__, physics
from .basis import LOG_MARK, LOG_SPARK, LOG_STATUS, LOG_SYNC, LogEntry
from .storage import DIAG_HEADER, ROH_HEADER, ZANGE_HEADER

MARK_TEXT = {1: "Taste T1", 2: "Taste T2", 3: "Taste T3", 4: "Taste T4", 10: "Marker (fern)"}
SFLAG_REJECTED = 1
GAP_S = 2.0                     # laengere Pausen ohne Funken werden uebersprungen


def log_to_rows(entries: List[LogEntry], params: physics.DynoParams, rate_hz: float = 50.0,
                timeout_s: float = 0.5) -> Dict:
    """-> {'header', 'rows', 'markers': [(t, text)], 'sparks', 'rejected', 'dauer_s'}"""
    entries = sorted(entries, key=lambda e: e.idx)
    sparks = [e for e in entries if e.art == LOG_SPARK]
    good = [e for e in sparks if not (int(e.num(1, 0)) & SFLAG_REJECTED)]
    rej = [e for e in sparks if int(e.num(1, 0)) & SFLAG_REJECTED]
    if not entries:
        raise ValueError("Log ist leer")
    t0_us = min(e.local_us for e in entries)
    ts = np.array([(e.local_us - t0_us) / 1e6 for e in good])
    peaks = np.array([e.num(2, np.nan) for e in good])
    tr = np.array([(e.local_us - t0_us) / 1e6 for e in rej])
    status = [((e.local_us - t0_us) / 1e6, e.num(1)) for e in entries if e.art == LOG_STATUS]
    t_end = max((e.local_us - t0_us) / 1e6 for e in entries)
    dt = 1.0 / rate_hz
    header = ROH_HEADER + DIAG_HEADER + ZANGE_HEADER + ["zange_akku_pct"]
    rows = []
    t = 0.0
    cycle = 0
    last_f = 0.0
    k = 0                         # naechster Funke
    akku = np.nan
    s_i = 0
    while t < t_end + dt:
        if k < len(ts) and ts[k] - t > GAP_S and (k == 0 or t - ts[k - 1] > timeout_s + dt):
            t = float(np.floor(ts[k] / dt) * dt)           # Pause ueberspringen
        t1 = t + dt
        a = int(np.searchsorted(ts, t, side="left"))
        b = int(np.searchsorted(ts, t1, side="left"))
        k = b
        flags = 0
        # Perioden, die im Fenster enden (inkl. der ab dem letzten Funken davor)
        lo = max(0, a - 1)
        per = np.diff(ts[lo:b]) if b - lo >= 2 else np.zeros(0)
        if len(per):
            last_f = len(per) / float(per.sum())
        elif a > 0 and t1 - ts[a - 1] > timeout_s:
            last_f = 0.0
            flags |= 1
        elif a == 0:
            last_f = 0.0
            flags |= 1
        else:
            flags |= 1 << 4                                   # keine ganze Periode im Fenster
        pk = peaks[a:b]
        pk = pk[np.isfinite(pk)]
        n_rej = int(np.count_nonzero((tr >= t) & (tr < t1)))
        while s_i < len(status) and status[s_i][0] < t1:
            akku = status[s_i][1]
            s_i += 1
        cycle = (cycle + 1) & 0xFFFF
        f_ign = last_f
        row = [cycle, f"{t1:.4f}", f"{rate_hz:g}", f"{f_ign:.3f}", "0", "nan", "0", "nan", "0.0000",
               f"{dt:.6f}", f"{f_ign * 60.0 / params.imp:.1f}",
               f"{t1 * 1000:.0f}", b - a, f"{per.min() * 1e6:.1f}" if len(per) else "0",
               f"{per.max() * 1e6:.1f}" if len(per) else "0", n_rej, 0, 0, 0, 0, f"{flags:d}",
               f"{pk.mean():.0f}" if len(pk) else "0", "1" if b > a else "0",
               "nan" if akku != akku else f"{akku:g}"]
        rows.append(row)
        t = t1
    markers = []
    for e in entries:
        if e.art == LOG_MARK:
            markers.append(((e.local_us - t0_us) / 1e6, MARK_TEXT.get(int(e.num(0, 0)), f"Marker {e.rest[:1]}")))
    return {"header": header, "rows": rows, "markers": markers, "sparks": len(good), "rejected": len(rej),
            "dauer_s": t_end}


def write_log_folder(entries: List[LogEntry], folder: str, params: Optional[physics.DynoParams] = None,
                     firmware: str = "", port: str = "", settings: Optional[Dict[str, str]] = None) -> str:
    """Zangen-Log als Dauermessungs-Ordner (roh.csv, lauf.json, marker.csv, zange_log.csv) ablegen."""
    params = params or physics.DynoParams()
    settings = settings or {}
    p = physics.DynoParams(**asdict(params))
    try:
        imp = float(settings.get("imp_per_rev_x10", "")) / 10.0
        if imp > 0:
            p.imp = imp
    except ValueError:
        pass
    os.makedirs(folder, exist_ok=True)
    res = log_to_rows(entries, p)
    with open(os.path.join(folder, "roh.csv"), "w", newline="") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(res["header"])
        w.writerows(res["rows"])
    with open(os.path.join(folder, "zange_log.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["idx", "art", "lokal_us", "werte"])
        for e in sorted(entries, key=lambda e: e.idx):
            w.writerow([e.idx, e.art, e.local_us, ";".join(e.rest)])
    if res["markers"]:
        with open(os.path.join(folder, "marker.csv"), "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh, delimiter=";")
            w.writerow(["zeit_s", "text"])
            for t, text in res["markers"]:
                w.writerow([f"{t:.3f}", text])
    syncs = sum(1 for e in entries if e.art == LOG_SYNC)
    meta = {
        "programm": f"PyST {__version__}", "art": "dauermessung", "quelle": "Zange-Log",
        "datum": _dt.datetime.now().isoformat(timespec="seconds"), "fahrzeug": "", "firmware": firmware,
        "port": port, "parameter": asdict(p), "klima": None, "com_verloren": 0, "telegramme": len(res["rows"]),
        "ergebnis": None, "diagnose": True, "zange_log": {
            "eintraege": len(entries), "funken": res["sparks"], "verworfen": res["rejected"],
            "marker": len(res["markers"]), "sync_paare": syncs, "dauer_s": round(res["dauer_s"], 3),
            "idx_von": min(e.idx for e in entries), "idx_bis": max(e.idx for e in entries),
            "einstellungen": settings},
        "hinweis": "Aus dem Log der Zange (Lokalzeit der Zange). Rolle = 0: Zange allein, ohne Prüfstand.",
        "marker": len(res["markers"]), "ereignisse": 0, "bursts": 0,
    }
    with open(os.path.join(folder, "lauf.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2, ensure_ascii=False)
    return folder


def log_folder_name(base_dir: str) -> str:
    return os.path.join(base_dir, _dt.datetime.now().strftime("%y%m%d_%H%M%S") + "_Zange-Log")
