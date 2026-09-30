"""
Datenbank (SQLite): Fahrzeuge, Setups, Laeufe.

- Fahrzeug- und Setup-Daten stehen als JSON in der Tabelle; welche Felder es gibt, legen
  VEHICLE_FIELDS / SETUP_FIELDS fest. Neue Felder brauchen keine Datenbank-Aenderung.
- Die Messdaten eines Laufs liegen als Dateien in einem Ordner (roh.csv, lauf.json, ...).
  Die Datenbank kennt Ordner, Ergebnis-Kennwerte und Zuordnung zu Fahrzeug/Setup.
"""
import datetime as _dt
import json
import os
import shutil
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# (Schluessel, Beschriftung, Typ: "text" | "num" | Liste = Auswahl)
VEHICLE_FIELDS = [
    ("Fahrzeug", [
        ("hersteller", "Hersteller", "text"),
        ("modell", "Modell", "text"),
        ("baujahr", "Baujahr", "text"),
        ("kennung", "Kennzeichen / FIN", "text"),
        ("gemisch", "Gemischaufbereitung", ["Vergaser", "EFI (rusEFI)", "EFI (Serie)", "sonstige"]),
        ("takt", "Motor", ["2-Takt", "4-Takt", "Elektro"]),
    ]),
]

SETUP_FIELDS = [
    ("Motor", [
        ("hubraum", "Hubraum [ccm]", "num"),
        ("bohrung", "Bohrung [mm]", "num"),
        ("hub", "Hub [mm]", "num"),
        ("cr", "Verdichtung CR [:1]", "num"),
        ("zylinder", "Zylinder / Kit", "text"),
        ("zylinderkopf", "Zylinderkopf / Quetschkante", "text"),
        ("steuerzeiten", "Steuerzeiten (Ein/Üb/Aus bzw. Nocke)", "text"),
        ("kuehlung", "Kühlung", ["Luft", "Wasser", "Öl/Luft"]),
    ]),
    ("Vergaser", [
        ("vergaser", "Vergaser (Typ)", "text"),
        ("durchlass", "Durchlass [mm]", "num"),
        ("hauptduese", "Hauptdüse", "text"),
        ("nadelduese", "Nadeldüse / Mischrohr", "text"),
        ("nadel", "Nadel", "text"),
        ("clip", "Clip-Position", "text"),
        ("leerlaufduese", "Leerlaufdüse", "text"),
        ("gemischschraube", "Gemischschraube [U offen]", "text"),
        ("schieber", "Schieber", "text"),
        ("schwimmer", "Schwimmerstand", "text"),
    ]),
    ("Einspritzung / ECU", [
        ("ecu", "ECU", "text"),
        ("tune", "Tune-Datei / Kalibrierung", "text"),
        ("einspritzduese", "Einspritzdüse [ccm/min]", "text"),
        ("kraftstoffdruck", "Kraftstoffdruck [bar]", "num"),
        ("drosselklappe", "Drosselklappe [mm]", "num"),
        ("lambda_ziel", "λ-Ziel Volllast", "num"),
    ]),
    ("Zündung", [
        ("zuendung", "Zündung (Typ)", "text"),
        ("zzp", "Zündzeitpunkt [° v. OT]", "text"),
        ("kerze", "Zündkerze / Wärmewert", "text"),
    ]),
    ("Antrieb", [
        ("primaer", "Primärübersetzung", "text"),
        ("sekundaer", "Sekundär / Endübersetzung", "text"),
        ("gang", "Gang (Messgang)", "text"),
        ("vario", "Variomatik", "text"),
        ("rollen", "Rollengewichte [g]", "text"),
        ("kupplung", "Kupplung / Federn", "text"),
        ("gegendruckfeder", "Gegendruckfeder", "text"),
        ("riemen", "Riemen / Kette", "text"),
        ("reifen", "Reifen / Luftdruck", "text"),
    ]),
    ("Ein- und Auslass", [
        ("luftfilter", "Luftfilter", "text"),
        ("ansaug", "Ansaugstutzen / Membran", "text"),
        ("auspuff", "Auspuff", "text"),
        ("kruemmer", "Krümmer / Dämpfer", "text"),
    ]),
    ("Kraftstoff", [
        ("kraftstoff", "Kraftstoff", "text"),
        ("oel", "Öl / Mischung", "text"),
    ]),
]

# Kurzinfo eines Setups in Listen und im Bericht
SETUP_SUMMARY_KEYS = ["hubraum", "cr", "vergaser", "hauptduese", "nadel", "clip", "ecu", "tune",
                      "zzp", "vario", "rollen", "auspuff"]


def all_setup_keys() -> List[str]:
    return [k for _, fields in SETUP_FIELDS for k, _, _ in fields]


def field_label(key: str) -> str:
    for _, fields in VEHICLE_FIELDS + SETUP_FIELDS:
        for k, label, _ in fields:
            if k == key:
                return label
    return key


def base_dir() -> str:
    """Datenordner ~/PyST. Gibt es noch den Ordner der Vorversion (~/SimpleDyno) und kein ~/PyST,
    wird der alte weiter benutzt – Datenbank und Pfade der Laeufe bleiben gueltig."""
    home = os.path.expanduser("~")
    new, old = os.path.join(home, "PyST"), os.path.join(home, "SimpleDyno")
    return old if os.path.isdir(old) and not os.path.isdir(new) else new


def db_file(folder: str) -> str:
    old = os.path.join(folder, "simpledyno.db")
    return old if os.path.exists(old) else os.path.join(folder, "pyst.db")


@dataclass
class RunRow:
    id: int
    datum: str
    name: str
    quelle: str
    ordner: str
    datei: str
    vehicle_id: Optional[int]
    setup_id: Optional[int]
    p_max: float
    n_pmax: float
    m_max: float
    n_mmax: float
    ka: float
    rate: float
    notiz: str
    farbe: str
    sichtbar: bool
    params: Dict[str, Any] = field(default_factory=dict)

    @property
    def path(self) -> str:
        """Datei/Ordner fuer storage.load_any()"""
        return os.path.join(self.ordner, self.datei) if self.datei else self.ordner


SCHEMA = """
CREATE TABLE IF NOT EXISTS vehicles(
    id INTEGER PRIMARY KEY, name TEXT, data TEXT, notiz TEXT, erstellt TEXT);
CREATE TABLE IF NOT EXISTS setups(
    id INTEGER PRIMARY KEY, vehicle_id INTEGER REFERENCES vehicles(id) ON DELETE CASCADE,
    name TEXT, data TEXT, notiz TEXT, erstellt TEXT);
CREATE TABLE IF NOT EXISTS runs(
    id INTEGER PRIMARY KEY, datum TEXT, name TEXT, quelle TEXT, ordner TEXT, datei TEXT,
    vehicle_id INTEGER REFERENCES vehicles(id) ON DELETE SET NULL,
    setup_id INTEGER REFERENCES setups(id) ON DELETE SET NULL,
    p_max REAL, n_pmax REAL, m_max REAL, n_mmax REAL, ka REAL, rate REAL,
    params TEXT, notiz TEXT, farbe TEXT, sichtbar INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
"""


class Database:
    def __init__(self, path: str = ""):
        self.path = path or db_file(base_dir())
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.con = sqlite3.connect(self.path)
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA foreign_keys = ON")
        self.con.executescript(SCHEMA)
        self.con.commit()
        self._migrate()

    def _migrate(self):
        # v2: automatisch vergebene Laufarben nicht mehr speichern (nur selbst gewaehlte)
        if not self.get_meta("colors_v2"):
            from .plots import OLD_AUTO_COLORS
            q = ",".join("?" * len(OLD_AUTO_COLORS))
            self.con.execute(f"UPDATE runs SET farbe='' WHERE farbe IN ({q})", OLD_AUTO_COLORS)
            self.set_meta("colors_v2", "1")

    def runs_dir(self) -> str:
        d = os.path.join(os.path.dirname(self.path), "messungen")
        os.makedirs(d, exist_ok=True)
        return d

    @staticmethod
    def _now() -> str:
        return _dt.datetime.now().isoformat(timespec="seconds")

    # ------------------------------------------------------------ Fahrzeuge
    def vehicles(self) -> List[sqlite3.Row]:
        return list(self.con.execute("SELECT * FROM vehicles ORDER BY name COLLATE NOCASE"))

    def vehicle(self, vid: int) -> Optional[Dict[str, Any]]:
        r = self.con.execute("SELECT * FROM vehicles WHERE id=?", (vid,)).fetchone()
        return None if r is None else {"id": r["id"], "notiz": r["notiz"] or "", **json.loads(r["data"] or "{}")}

    @staticmethod
    def vehicle_name(d: Dict[str, Any]) -> str:
        return " ".join(x for x in (d.get("hersteller", ""), d.get("modell", ""), d.get("baujahr", "")) if x) \
            or "Fahrzeug ohne Namen"

    def save_vehicle(self, data: Dict[str, Any], vid: Optional[int] = None) -> int:
        notiz = data.pop("notiz", "")
        name = self.vehicle_name(data)
        if vid:
            self.con.execute("UPDATE vehicles SET name=?, data=?, notiz=? WHERE id=?",
                             (name, json.dumps(data, ensure_ascii=False), notiz, vid))
        else:
            vid = self.con.execute("INSERT INTO vehicles(name, data, notiz, erstellt) VALUES(?,?,?,?)",
                                   (name, json.dumps(data, ensure_ascii=False), notiz, self._now())).lastrowid
        self.con.commit()
        return vid

    def delete_vehicle(self, vid: int):
        self.con.execute("DELETE FROM vehicles WHERE id=?", (vid,))
        self.con.commit()

    # ------------------------------------------------------------ Setups
    def setups(self, vehicle_id: Optional[int]) -> List[sqlite3.Row]:
        return list(self.con.execute("SELECT * FROM setups WHERE vehicle_id=? ORDER BY erstellt DESC",
                                     (vehicle_id,)))

    def setup(self, sid: int) -> Optional[Dict[str, Any]]:
        r = self.con.execute("SELECT * FROM setups WHERE id=?", (sid,)).fetchone()
        if r is None:
            return None
        return {"id": r["id"], "vehicle_id": r["vehicle_id"], "name": r["name"] or "",
                "notiz": r["notiz"] or "", **json.loads(r["data"] or "{}")}

    def save_setup(self, vehicle_id: int, name: str, data: Dict[str, Any], notiz: str = "",
                   sid: Optional[int] = None) -> int:
        payload = json.dumps(data, ensure_ascii=False)
        if sid:
            self.con.execute("UPDATE setups SET name=?, data=?, notiz=? WHERE id=?", (name, payload, notiz, sid))
        else:
            sid = self.con.execute("INSERT INTO setups(vehicle_id, name, data, notiz, erstellt) VALUES(?,?,?,?,?)",
                                   (vehicle_id, name, payload, notiz, self._now())).lastrowid
        self.con.commit()
        return sid

    def copy_setup(self, sid: int, new_name: str) -> int:
        s = self.setup(sid)
        data = {k: v for k, v in s.items() if k not in ("id", "vehicle_id", "name", "notiz")}
        return self.save_setup(s["vehicle_id"], new_name, data, s["notiz"])

    def delete_setup(self, sid: int):
        self.con.execute("DELETE FROM setups WHERE id=?", (sid,))
        self.con.commit()

    # ------------------------------------------------------------ Laeufe
    def _row(self, r) -> RunRow:
        return RunRow(id=r["id"], datum=r["datum"] or "", name=r["name"] or "", quelle=r["quelle"] or "",
                      ordner=r["ordner"] or "", datei=r["datei"] or "", vehicle_id=r["vehicle_id"],
                      setup_id=r["setup_id"], p_max=r["p_max"] or 0.0, n_pmax=r["n_pmax"] or 0.0,
                      m_max=r["m_max"] or 0.0, n_mmax=r["n_mmax"] or 0.0, ka=r["ka"] or 0.0,
                      rate=r["rate"] or 0.0, notiz=r["notiz"] or "", farbe=r["farbe"] or "",
                      sichtbar=bool(r["sichtbar"]), params=json.loads(r["params"] or "{}"))

    def runs(self, vehicle_id: Optional[int] = None, setup_id: Optional[int] = None) -> List[RunRow]:
        q, args = "SELECT * FROM runs", []
        if setup_id:
            q, args = q + " WHERE setup_id=?", [setup_id]
        elif vehicle_id:
            q, args = q + " WHERE vehicle_id=?", [vehicle_id]
        return [self._row(r) for r in self.con.execute(q + " ORDER BY datum DESC", args)]

    def run(self, rid: int) -> Optional[RunRow]:
        r = self.con.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone()
        return None if r is None else self._row(r)

    def add_run(self, ordner: str, datei: str, quelle: str, name: str, datum: str, result=None,
                params: Optional[Dict[str, Any]] = None, rate: float = 0.0, vehicle_id=None, setup_id=None,
                notiz: str = "", farbe: str = "", sichtbar: bool = True) -> int:
        vals = (0.0, 0.0, 0.0, 0.0, 0.0) if result is None else \
            (result.p_max, result.n_pmax, result.m_max, result.n_mmax, result.ka)
        rid = self.con.execute(
            "INSERT INTO runs(datum, name, quelle, ordner, datei, vehicle_id, setup_id, p_max, n_pmax, m_max,"
            " n_mmax, ka, rate, params, notiz, farbe, sichtbar) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (datum, name, quelle, ordner, datei, vehicle_id, setup_id, *vals, rate,
             json.dumps(params or {}), notiz, farbe, int(sichtbar))).lastrowid
        self.con.commit()
        return rid

    def update_run(self, rid: int, **kw):
        if not kw:
            return
        if "params" in kw:
            kw["params"] = json.dumps(kw["params"])
        if "sichtbar" in kw:
            kw["sichtbar"] = int(kw["sichtbar"])
        cols = ", ".join(f"{k}=?" for k in kw)
        self.con.execute(f"UPDATE runs SET {cols} WHERE id=?", (*kw.values(), rid))
        self.con.commit()

    def delete_run(self, rid: int, delete_files: bool = False):
        r = self.run(rid)
        self.con.execute("DELETE FROM runs WHERE id=?", (rid,))
        self.con.commit()
        if delete_files and r and r.ordner.startswith(self.runs_dir()) and os.path.isdir(r.ordner):
            shutil.rmtree(r.ordner, ignore_errors=True)

    # ------------------------------------------------------------ Import
    def import_file(self, path: str, vehicle_id=None, setup_id=None) -> int:
        """LabVIEW-XML oder PyST-Ordner in die Datenbank uebernehmen (Datei wird kopiert)."""
        from . import physics, storage
        run = storage.load_any(path)
        res = storage.recompute(run)
        src = path if not os.path.isdir(path) else os.path.join(path, "lauf.json")
        name = os.path.splitext(os.path.basename(src))[0] if run["source"] == "LabVIEW" else run["name"]
        target = os.path.join(self.runs_dir(), name.strip().replace(" ", "_"))
        n = 1
        while os.path.exists(target):
            n += 1
            target = os.path.join(self.runs_dir(), f"{name.strip().replace(' ', '_')}_{n}")
        if os.path.isdir(path):
            shutil.copytree(path, target)
            datei = "lauf.json"
        else:
            os.makedirs(target)
            datei = os.path.basename(path)
            shutil.copy2(path, os.path.join(target, datei))
        notiz = " ".join(x for x in (run.get("vehicle", ""), run.get("title", "")) if x)
        datum = run.get("date", "") or self._now()
        for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%Y %H:%M:%S"):
            try:
                datum = _dt.datetime.strptime(datum.strip(), fmt).isoformat(timespec="seconds")
                break
            except ValueError:
                pass
        return self.add_run(target, datei, run["source"], name, datum, res, params=run["params"].__dict__,
                            rate=physics.sample_rate(run["dt"]), vehicle_id=vehicle_id, setup_id=setup_id,
                            notiz=notiz, sichtbar=False)

    # ------------------------------------------------------------ Einstellungen
    def get_meta(self, key: str, default: str = "") -> str:
        r = self.con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default

    def set_meta(self, key: str, value: str):
        self.con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?,?)", (key, value))
        self.con.commit()
