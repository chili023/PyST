"""
Laeufe speichern und laden.

Ein Lauf = Ordner  messungen/YYMMDD_HHMMSS_<PS>_<n>/  mit
  roh.csv       alle Telegramme (Zyklus, Zeit, Frequenzen, EGT, AFR) + nRolle, dT
                (+ Spalten rusefi_<Kanal>: ECU-Werte auf die Telegramm-Zeitpunkte gelegt)
  rusefi.csv    ECU-Werte in ihrer eigenen Abtastung (nur mit rusEFI)
  kurve.csv     ausgewertete Kurve (n, PS, Nm, n gemessen, AFR, EGT)
  lauf.json     Parameter, Klima, Ergebnis, Firmware-Info
  lauf.xml      dasselbe im LabVIEW-3.2.1-Format (in LabVIEW-Recalc oeffnbar)
  marker.csv    Marker mit Text (Dauermessung), ereignisse.csv (Software-Trigger), bursts/burst_NNN.csv
Dauermessung = Ordner  messungen/YYMMDD_HHMMSS_Dauermessung/  (siehe recorder.py): waehrend der Messung
  teil_NNNN.csv in Abschnitten, beim Beenden zu roh.csv zusammengefasst.
Geladen werden PyST-Ordner (bzw. deren lauf.json), .pyst-Dateien (ZIP, siehe unten), LabVIEW-XML-Dateien
und Rohmitschnitte aus Terminalprogrammen (TeraTerm, HTerm, …) von Mega und F446.

.pyst = ein oder mehrere Laeufe/Dauermessungen in einer ZIP-Datei zum Weitergeben:
  inhalt.json           Liste der Laeufe mit Fahrzeug, Setup, Notiz, Parametern
  01_<Name>/…           die Dateien des Laufordners (roh.csv, lauf.json, kurve.csv, marker.csv, bursts/ …)
"""
import csv
import datetime as _dt
import io
import json
import os
import re
import zipfile
from dataclasses import asdict
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

from . import __version__, lvxml, physics, rusefi

PYST_EXT = ".pyst"
ROH_HEADER = ["zyklus", "pc_zeit_s", "messfrequenz_hz", "zuendung_hz", "rolle_hz",
              "egt1_c", "afr", "egt2_c", "n_rolle_1_min", "dt_s", "n_zuendung_1_min"]
DIAG_HEADER = ["diag_t_ms", "diag_ign_n", "diag_ign_pmin_us", "diag_ign_pmax_us", "diag_ign_rej",
               "diag_roll_n", "diag_roll_pmin_us", "diag_roll_pmax_us", "diag_roll_rej", "diag_flags"]


def frame_row(f, t0: float, p: physics.DynoParams, diag: bool = False) -> list:
    """Eine Zeile der roh.csv aus einem Telegramm (link.Frame)."""
    d = 1.0 / f.rate if f.rate > 0 else 0.0
    row = [f.cycle, f"{f.t - t0:.4f}", f.rate, f.ign_hz, f.roll_hz, f.egt1, f.afr, f.egt2,
           f"{f.roll_hz * 60.0 / p.inkr:.4f}", f"{d:.6f}", f"{f.ign_hz * 60.0 / p.imp:.1f}"]
    if diag:
        g = f.diag or {}
        row += [f"{g.get(k[5:], float('nan')):g}" for k in DIAG_HEADER]
    return row


def default_dir() -> str:
    from .db import base_dir
    return os.path.join(base_dir(), "messungen")


def save_run(ctrl, vehicle: str = "", firmware: str = "", base_dir: str = "") -> str:
    res = ctrl.result
    p = ctrl.params
    stamp = _dt.datetime.now().strftime("%y%m%d_%H%M%S")
    name = stamp + (f"_{res.p_max:.1f}_{res.n_pmax:.0f}".replace(".", ",") if res else "_ohne_Ergebnis")
    folder = os.path.join(base_dir or default_dir(), name)
    os.makedirs(folder, exist_ok=True)

    n_roll, dt, n_meas, afr, egt = ctrl.arrays()
    t0 = ctrl.frames[0].t if ctrl.frames else 0.0
    ecu = getattr(ctrl, "ecu_data", None)
    ecu_cols = rusefi.resample(ecu, np.array([f.t for f in ctrl.frames])) if ecu and ctrl.frames else {}
    has_diag = any(getattr(f, "diag", None) for f in ctrl.frames)
    with open(os.path.join(folder, "roh.csv"), "w", newline="") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(ROH_HEADER + (DIAG_HEADER if has_diag else []) + list(ecu_cols))
        for i, f in enumerate(ctrl.frames):
            w.writerow(frame_row(f, t0, p, has_diag) + [f"{v[i]:.6g}" for v in ecu_cols.values()])
    if ecu:
        with open(os.path.join(folder, "rusefi.csv"), "w", newline="") as fh:
            w = csv.writer(fh, delimiter=";")
            w.writerow(["pc_zeit_s"] + [c["name"] for c in ecu["channels"]])
            for t, row in zip(ecu["t"], ecu["values"]):
                w.writerow([f"{t - t0:.4f}"] + [f"{v:.6g}" for v in row])
    if res is not None:
        with open(os.path.join(folder, "kurve.csv"), "w", newline="") as fh:
            w = csv.writer(fh, delimiter=";")
            w.writerow(["n_1_min", "leistung_ps", "drehmoment_nm", "n_zuendung_1_min", "afr", "egt_c"])
            for row in zip(res.n, res.ps, res.nm, res.n_meas, res.afr, res.egt):
                w.writerow([f"{x:.3f}" for x in row])

    meta: Dict[str, Any] = {
        "programm": f"PyST {__version__}",
        "datum": _dt.datetime.now().isoformat(timespec="seconds"),
        "fahrzeug": vehicle,
        "firmware": firmware,
        "port": ctrl.link.port,
        "parameter": asdict(p),
        "klima": ctrl.climate,
        "com_verloren": ctrl.link.lost,
        "telegramme": len(ctrl.frames),
        "rusefi": None if not ecu else {
            "signatur": ecu["signature"], "ini": os.path.basename(ecu["ini"]),
            "abtastrate_hz": round(ecu["rate_hz"], 1), "werte": len(ecu["t"]),
            "kanaele": {rusefi.COLUMN_PREFIX + c["name"]: {"name": c["name"], "label": c["label"],
                                                           "einheit": c["unit"]} for c in ecu["channels"]},
        },
        "ergebnis": None if res is None else {
            "p_max_ps": res.p_max, "n_pmax": res.n_pmax, "m_max_nm": res.m_max, "n_mmax": res.n_mmax,
            "v_max_kmh": res.v_max, "ka": res.ka, "strecke_m": res.distance_m, "ende": res.end_reason,
            "text": res.summary(),
        },
    }
    with open(os.path.join(folder, "lauf.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2, ensure_ascii=False)
    # LabVIEW-Datei mit Filtern in Messpunkten (LabVIEW kennt keine Sekunden)
    lvxml.write_run(os.path.join(folder, "lauf.xml"), physics.effective(p, physics.sample_rate(dt)),
                    n_roll, n_meas, afr, egt, dt,
                    vehicle=vehicle, distance_m=res.distance_m if res else 0.0, port=ctrl.link.port)
    return folder


def _float(v: str) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return float("nan")


def _read_csv_cols(text: str) -> Dict[str, list]:
    cols: Dict[str, list] = {}
    rd = csv.DictReader(io.StringIO(text), delimiter=";")
    for row in rd:
        for k, v in row.items():
            if k is not None:
                cols.setdefault(k, []).append(_float(v))
    return cols


def _read_side_files(read: Callable[[str], Optional[str]], names: List[str]) -> Dict[str, Any]:
    """Marker, Ereignisse, Notizen und Bursts eines Laufordners (alle optional)."""
    out: Dict[str, Any] = {"markers": [], "events": [], "bursts": [], "notes": ""}
    txt = read("marker.csv")
    if txt:
        for row in csv.reader(io.StringIO(txt), delimiter=";"):
            if len(row) >= 2 and row[0] != "zeit_s":
                out["markers"].append((_float(row[0]), row[1]))
    txt = read("ereignisse.csv")
    if txt:
        for row in csv.reader(io.StringIO(txt), delimiter=";"):
            if len(row) >= 3 and row[0] != "zeit_s":
                out["events"].append((_float(row[0]), row[1], row[2]))
    out["notes"] = read("notizen.txt") or ""
    for n in sorted(x for x in names if x.startswith("bursts/") and x.endswith(".csv")):
        txt = read(n)
        if txt:
            out["bursts"].append(burst_from_csv(txt, os.path.basename(n)))
    return out


def burst_to_csv(burst) -> str:
    """Burst -> CSV (Kopfzeilen mit '#' tragen die Parameter)."""
    buf = io.StringIO()
    req = ";".join(f"{k}={v}" for k, v in burst.request.items())
    buf.write(f"# n={burst.n};tick_ns={burst.tick_ns:g};trig_idx={burst.trig_idx};ch={burst.ch};lost={burst.lost};"
              f"pc_zeit={burst.t_pc:.3f};{req}\n")
    w = csv.writer(buf, delimiter=";")
    w.writerow(["idx", "kanal", "pegel", "ticks", "zeit_s"])
    w.writerows(burst.to_rows())
    return buf.getvalue()


def burst_from_csv(text: str, name: str = ""):
    from .diag import Burst, parse_kv
    lines = text.splitlines()
    kv = parse_kv(lines[0][1:]) if lines and lines[0].startswith("#") else {}
    b = Burst(n=int(kv.get("n", 0) or 0), tick_ns=float(kv.get("tick_ns", 62.5) or 62.5),
              trig_idx=int(kv.get("trig_idx", -1) or -1), ch=kv.get("ch", "A"), lost=int(kv.get("lost", 0) or 0),
              t_pc=float(kv.get("pc_zeit", 0) or 0), complete=True,
              request={k: v for k, v in kv.items() if k in ("edge", "trig", "pre", "anzahl", "name")})
    b.request.setdefault("name", name)
    for row in csv.reader(lines[1:] if kv else lines, delimiter=";"):
        if len(row) >= 4 and row[0].isdigit():
            b.edges.append((int(row[0]), row[1], int(row[2]), int(row[3])))
    return b


def _load_pyst_files(read: Callable[[str], Optional[str]], names: List[str], name: str,
                     xml_path: Callable[[str], str]) -> Dict[str, Any]:
    """PyST-Laufordner (Dateien ueber read(name) -> Text) laden."""
    meta_txt = read("lauf.json") or read("dauer.json")
    if meta_txt is None:
        xmls = [n for n in names if n.lower().endswith(".xml") and "/" not in n]
        if xmls:                                              # LabVIEW-Lauf in einer .pyst-Datei
            r = lvxml.read_run(xml_path(xmls[0]))
            r.update(source="LabVIEW", name=os.path.splitext(xmls[0])[0], kind="lauf")
            r.update(_read_side_files(read, names))
            return r
        raise ValueError(f"{name}: weder lauf.json noch LabVIEW-XML gefunden")
    meta = json.loads(meta_txt)
    roh = read("roh.csv")
    if roh is not None:
        cols = _read_csv_cols(roh)
    else:                                                     # Dauermessung nach Absturz: Abschnitte
        cols = {}
        for n in sorted(x for x in names if re.match(r"teil_\d+\.csv$", x)):
            for k, v in _read_csv_cols(read(n) or "").items():
                cols.setdefault(k, []).extend(v)
        if not cols:
            raise ValueError(f"{name}: keine Messdaten (roh.csv/teil_*.csv)")
    p = physics.DynoParams(**{k: v for k, v in meta["parameter"].items()
                              if k in physics.DynoParams.__dataclass_fields__})
    r = {
        "params": p, "vehicle": meta.get("fahrzeug", ""), "title": "", "date": meta.get("datum", ""),
        "n_roll": np.array(cols["n_rolle_1_min"]), "dt": np.array(cols["dt_s"]),
        "n_meas": np.array(cols["n_zuendung_1_min"]), "afr": np.array(cols["afr"]),
        "egt": np.array(cols["egt1_c"]), "source": "PyST", "name": name,
        "meta": meta, "columns": {k: np.array(v) for k, v in cols.items()},
        "kind": meta.get("art", "lauf"),
    }
    r.update(_read_side_files(read, names))
    return r


def _folder_reader(folder: str):
    def read(n: str) -> Optional[str]:
        p = os.path.join(folder, n)
        if not os.path.isfile(p):
            return None
        with open(p, encoding="utf-8", errors="replace", newline="") as fh:
            return fh.read()
    names = []
    for root, _dirs, files in os.walk(folder):
        for f in files:
            names.append(os.path.relpath(os.path.join(root, f), folder).replace(os.sep, "/"))
    return read, names


def is_terminal_log(path: str) -> bool:
    """Rohmitschnitt eines Terminalprogramms? (mind. 5 Telegrammzeilen in den ersten 64 KB)"""
    if os.path.isdir(path) or path.lower().endswith((".xml", ".json", PYST_EXT)):
        return False
    try:
        with open(path, "rb") as fh:
            head = fh.read(65536).decode("latin-1")
    except OSError:
        return False
    if head.startswith("zyklus;"):                    # roh.csv von PyST
        return False
    n = 0
    for line in re.split(r"\r\n|\r|\n", head):
        if _TELEGRAM.match(_TS.sub("", line.strip())):
            n += 1
            if n >= 5:
                return True
    return False


def load_any(path: str) -> Dict[str, Any]:
    """Laedt LabVIEW-XML, PyST-Ordner, lauf.json, .pyst (erster Lauf) oder Terminal-Mitschnitt."""
    if path.lower().endswith(PYST_EXT):
        return load_pyst(path)[0]
    if os.path.isdir(path):
        read, names = _folder_reader(path)
        return _load_pyst_files(read, names, os.path.basename(os.path.normpath(path)),
                                lambda n: os.path.join(path, n))
    if path.lower().endswith(".xml"):
        r = lvxml.read_run(path)
        r["source"] = "LabVIEW"
        r["name"] = os.path.basename(path)
        r["kind"] = "lauf"
        r.update(_read_side_files(lambda n: None, []))
        return r
    if path.lower().endswith(".json"):
        folder = os.path.dirname(os.path.abspath(path))
        read, names = _folder_reader(folder)
        return _load_pyst_files(read, names, os.path.basename(folder), lambda n: os.path.join(folder, n))
    if is_terminal_log(path):
        return parse_terminal_log(path)
    raise ValueError(f"{os.path.basename(path)}: unbekanntes Dateiformat")


# ---------------------------------------------------------------------------------------------- .pyst
def _zip_name(name: str) -> str:
    return re.sub(r"[^\w.,+-]+", "_", name).strip("_") or "lauf"


def export_pyst(path: str, items: List[Dict[str, Any]]) -> str:
    """Laeufe/Dauermessungen in eine .pyst-Datei. items: je Lauf {ordner, name, datum, quelle, art,
    fahrzeug (dict), setup (dict), notiz, farbe, parameter (dict, Parameter der Datenbank)}."""
    if not path.lower().endswith(PYST_EXT):
        path += PYST_EXT
    index = []
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for k, it in enumerate(items, 1):
            sub = f"{k:02d}_{_zip_name(it.get('name', ''))}"
            folder = it["ordner"]
            _read, names = _folder_reader(folder)
            for n in names:
                if n.endswith((".png", ".tmp")) or n.startswith("."):
                    continue
                z.write(os.path.join(folder, n), f"{sub}/{n}")
            entry = {k2: v for k2, v in it.items() if k2 != "ordner"}
            entry["ordner"] = sub
            index.append(entry)
        z.writestr("inhalt.json", json.dumps({
            "format": "pyst", "version": 1, "programm": f"PyST {__version__}",
            "erstellt": _dt.datetime.now().isoformat(timespec="seconds"), "laeufe": index,
        }, indent=2, ensure_ascii=False))
    return path


def pyst_index(path: str) -> Dict[str, Any]:
    with zipfile.ZipFile(path) as z:
        return json.loads(z.read("inhalt.json").decode("utf-8"))


def load_pyst(path: str) -> List[Dict[str, Any]]:
    """Alle Laeufe einer .pyst-Datei (ohne Entpacken). Jeder Lauf bekommt 'pyst' = Eintrag aus inhalt.json."""
    import tempfile
    out = []
    with zipfile.ZipFile(path) as z:
        idx = json.loads(z.read("inhalt.json").decode("utf-8"))
        all_names = z.namelist()
        for entry in idx.get("laeufe", []):
            sub = entry["ordner"].rstrip("/") + "/"
            names = [n[len(sub):] for n in all_names if n.startswith(sub) and not n.endswith("/")]

            def read(n: str, sub=sub) -> Optional[str]:
                try:
                    return z.read(sub + n).decode("utf-8", errors="replace")
                except KeyError:
                    return None

            def xml_path(n: str, sub=sub) -> str:
                tmp = tempfile.NamedTemporaryFile(suffix=".xml", delete=False)
                tmp.write(z.read(sub + n))
                tmp.close()
                return tmp.name

            r = _load_pyst_files(read, names, entry.get("name") or sub.rstrip("/"), xml_path)
            if entry.get("parameter"):
                r["params"] = physics.DynoParams(**{k: v for k, v in entry["parameter"].items()
                                                    if k in physics.DynoParams.__dataclass_fields__})
            r["pyst"] = entry
            out.append(r)
    if not out:
        raise ValueError(f"{os.path.basename(path)}: enthält keine Läufe")
    return out


def extract_pyst_entry(path: str, entry: Dict[str, Any], target: str):
    """Ordner eines Laufs aus der .pyst-Datei nach target entpacken."""
    sub = entry["ordner"].rstrip("/") + "/"
    with zipfile.ZipFile(path) as z:
        for n in z.namelist():
            if n.startswith(sub) and not n.endswith("/"):
                rel = n[len(sub):]
                if ".." in rel.split("/"):
                    continue                                   # nichts ausserhalb des Ziels schreiben
                dest = os.path.join(target, *rel.split("/"))
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with open(dest, "wb") as fh:
                    fh.write(z.read(n))


# ---------------------------------------------------------------------------------------------- Terminal-Mitschnitt
_TS = re.compile(r"^\[(\d{4}-\d\d-\d\d[ T]\d\d:\d\d:\d\d(?:[.,]\d+)?)\]\s*")
_TELEGRAM = re.compile(r"^(\d+;-?[\d.]+;[-\d.naife]+;[-\d.naife]+(;.*)?|D;\d+;.*)$")


def parse_terminal_log(path: str, params: Optional[physics.DynoParams] = None) -> Dict[str, Any]:
    """TeraTerm-/Terminal-Mitschnitt des Mega (3.x) oder der F446 -> Lauf (wie load_any).
    Zeilenende \r, \n oder \r\n; optional Zeitstempel "[yyyy-mm-dd hh:mm:ss.fff]" am Zeilenanfang;
    Antworten auf 'v' (Firmware) und 'e' (Klima) davor werden ausgewertet. Pruefstandsparameter stehen
    nicht im Mitschnitt: params (Abfrage in der Oberflaeche) oder PyST-Standard mit Hinweis."""
    from .link import parse_line
    with open(path, "rb") as fh:
        raw = fh.read().decode("latin-1")
    info, klima, frames, stamps = [], None, [], []
    first_stamp = None
    for line in re.split(r"\r\n|\r|\n", raw):
        line = line.strip()
        if not line:
            continue
        m = _TS.match(line)
        t = None
        if m:
            try:
                t = _dt.datetime.strptime(m.group(1).replace("T", " ").replace(",", "."),
                                          "%Y-%m-%d %H:%M:%S.%f" if "." in m.group(1) or "," in m.group(1)
                                          else "%Y-%m-%d %H:%M:%S").timestamp()
            except ValueError:
                t = None
            line = line[m.end():].strip()
            first_stamp = first_stamp if first_stamp is not None else t
        r = parse_line(line, t if t is not None else 0.0)
        if r is None:
            continue
        kind, obj = r
        if kind in ("F", "D"):
            frames.append(obj)
            stamps.append(t)
        elif kind == "T":
            f = line.split(";")
            if len(f) == 3 and not frames:
                try:
                    klima = [float(x) for x in f]
                    continue
                except ValueError:
                    pass
            if not frames:
                info.append(line.rstrip(";"))
    if not frames:
        raise ValueError(f"{os.path.basename(path)}: keine Telegramme gefunden")
    hint = ""
    if params is None:
        params = physics.DynoParams()
        hint = "Prüfstandsparameter = PyST-Standard (nicht aus dem Mitschnitt) – bitte prüfen und neu rechnen"
    p = physics.DynoParams(**asdict(params))
    if klima:
        p.temp_c, p.p_mbar = klima[0], klima[1] / 100.0
    # PC-Zeit: Zeitstempel des Terminals, sonst aus der Messfrequenz aufsummiert
    have_ts = all(s is not None for s in stamps)
    acc = 0.0
    for f, st in zip(frames, stamps):
        if have_ts:
            f.t = st - stamps[0]
        else:
            f.t = acc
            acc += 1.0 / f.rate if f.rate > 0 else 0.0
    has_diag = any(f.diag for f in frames)
    header = ROH_HEADER + (DIAG_HEADER if has_diag else [])
    rows = [frame_row(f, 0.0, p, has_diag) for f in frames]
    cols = {h: np.array([_float(str(r[i])) for r in rows]) for i, h in enumerate(header)}
    date = _dt.datetime.fromtimestamp(first_stamp if first_stamp is not None else os.path.getmtime(path))
    meta = {"programm": f"PyST {__version__} – Import Terminal-Mitschnitt", "quelle": os.path.basename(path),
            "datum": date.isoformat(timespec="seconds"), "fahrzeug": "", "firmware": " | ".join(info),
            "port": "Terminal-Mitschnitt", "parameter": asdict(p), "klima": klima, "com_verloren": 0,
            "telegramme": len(rows), "hinweis": hint, "ergebnis": None, "art": "lauf"}
    r = {"params": p, "vehicle": "", "title": "", "date": meta["datum"],
         "n_roll": cols["n_rolle_1_min"], "dt": cols["dt_s"], "n_meas": cols["n_zuendung_1_min"],
         "afr": cols["afr"], "egt": cols["egt1_c"], "source": "Terminal",
         "name": os.path.splitext(os.path.basename(path))[0], "meta": meta, "columns": cols, "kind": "lauf",
         "rows": rows, "header": header}
    r.update(_read_side_files(lambda n: None, []))
    return r


def write_imported_log(run: Dict[str, Any], folder: str):
    """Ergebnis von parse_terminal_log als PyST-Laufordner ablegen (roh.csv + lauf.json)."""
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "roh.csv"), "w", newline="") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(run["header"])
        w.writerows(run["rows"])
    meta = dict(run["meta"])
    meta["parameter"] = asdict(run["params"])
    with open(os.path.join(folder, "lauf.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2, ensure_ascii=False)


def recompute(run: Dict[str, Any], override: physics.DynoParams = None):
    p = override or run["params"]
    return physics.evaluate(run["n_roll"], run["dt"], p, run["n_meas"], run["afr"], run["egt"],
                            require_end=False)
