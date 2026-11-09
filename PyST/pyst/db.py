"""
Datenbank (SQLite): Fahrzeuge, Setups, Laeufe.

- Fahrzeug- und Setup-Daten stehen als JSON in der Tabelle; welche Felder es gibt, legen
  VEHICLE_FIELDS / SETUP_FIELDS fest. Neue Felder brauchen keine Datenbank-Aenderung.
- Die Messdaten eines Laufs liegen als Dateien in einem Ordner (roh.csv, lauf.json, ...).
  Die Datenbank kennt Ordner, Ergebnis-Kennwerte und Zuordnung zu Fahrzeug/Setup.
- Je Lauf ein Schnappschuss der Setup-Werte (setup_werte) mit dem Wert davor; Art des Laufs
  (lauf / dauermessung / mittelwert, Mittelwert-Laeufe siehe mittelwert.py).
- Je Fahrzeug gespeicherte Ansicht (Tabelle vehicle_prefs, globaler Standard in meta):
  Schnellwert-Plaetze, Live-Plaetze, gesperrte Plaetze im Messfenster.
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


# Arten eines Laufs (RunRow.art)
ART_LAUF = "lauf"
ART_DAUERMESSUNG = "dauermessung"
ART_MITTELWERT = "mittelwert"
ARTEN = (ART_LAUF, ART_DAUERMESSUNG, ART_MITTELWERT)
QUELLE_MITTELWERT = "Mittelwert"


def art_aus_quelle(quelle: str) -> str:
    """Art fuer Laeufe ohne gespeicherte Art (alte Datenbanken)."""
    if quelle == "Dauermessung":
        return ART_DAUERMESSUNG
    if quelle == QUELLE_MITTELWERT:
        return ART_MITTELWERT
    return ART_LAUF


def wert_text(v: Any) -> str:
    """Setup-Wert als Text zum Vergleichen und Anzeigen (18.0 -> "18", None -> "")."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "ja" if v else "nein"
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else f"{v:g}"
    return str(v).strip()


def setup_schnappschuss(setup: Optional[Dict[str, Any]], felder: List[str],
                        geaendert: Optional[Dict[str, Any]] = None) -> List[Dict[str, str]]:
    """Setup-Werte eines Laufs zum Speichern in runs.setup_werte.
    setup: gespeichertes Setup (db.setup) oder {}; felder: Schluessel aus SETUP_FIELDS (leere werden
    uebersprungen); geaendert: {feld: neuer_wert} (nur fuer diesen Lauf bzw. noch nicht gespeichert).
    Ergebnis: [{"feld", "wert", "vorher"}], vorher = Wert im Setup, nur wenn er abweicht (sonst "")."""
    setup = setup or {}
    geaendert = geaendert or {}
    out = []
    for f in dict.fromkeys(f for f in felder if f):
        alt = wert_text(setup.get(f))
        if f in geaendert:
            neu = wert_text(geaendert[f])
            out.append({"feld": f, "wert": neu, "vorher": alt if neu != alt else ""})
        else:
            out.append({"feld": f, "wert": alt, "vorher": ""})
    return out


def _werte_dict(werte) -> Dict[str, str]:
    """setup_werte (Liste von {"feld", "wert"}) oder schon ein Dict -> {feld: wert}"""
    if not werte:
        return {}
    if isinstance(werte, dict):
        return {k: wert_text(v) for k, v in werte.items()}
    return {e["feld"]: wert_text(e.get("wert")) for e in werte if isinstance(e, dict) and e.get("feld")}


def schnellwert_abweichungen(vorher_lauf_werte, werte) -> Dict[str, str]:
    """Fuer die Laufleiste ("140 ↑ 135"): je Feld, das sich gegenueber dem Lauf davor geaendert hat,
    der alte Wert. Beide Argumente: setup_werte-Liste oder {feld: wert}. Felder, die einer der beiden
    Laeufe nicht kennt, zaehlen nicht als Abweichung."""
    alt, neu = _werte_dict(vorher_lauf_werte), _werte_dict(werte)
    return {f: alt[f] for f, w in neu.items() if f in alt and alt[f] != w}


def schnellwert_pfeil(neu: Any, alt: Any) -> str:
    """Pfeil fuer die Laufleiste: "↑" / "↓" bei Zahlen (Komma erlaubt), sonst "→"; "" wenn gleich."""
    neu, alt = wert_text(neu), wert_text(alt)
    if neu == alt:
        return ""
    try:
        a, b = float(neu.replace(",", ".")), float(alt.replace(",", "."))
    except ValueError:
        return "→"
    return "↑" if a > b else ("↓" if a < b else "→")


# ------------------------------------------------------------ Ansicht je Fahrzeug
SCHNELLWERT_ANZAHL = 3          # Standard-Anzahl (frueher fest)
SCHNELLWERT_MAX = 8             # hoechstens so viele Plaetze je Fahrzeug
SCHNELLWERT_STANDARD = ["hauptduese", "clip", "zzp"]

LIVE_ANZAHL = 5                 # Standard-Anzahl der Live-Plaetze (frueher fest)
LIVE_MAX = 12                   # hoechstens so viele Live-Plaetze je Fahrzeug
LIVE_STUFEN = ("ok", "warn", "err", "cold")
ECU_PREFIX = "ecu:"          # Live-Kanal einer ECU: "ecu:" + rusEFI-Kanalname (z.B. "ecu:coolant", "ecu:MAPValue")
# Live-Kanaele des Messboards (Schluessel wie gui.gauges) -> (Name, Einheit); "" = Platz leer lassen
LIVE_KANAELE = {
    "n": ("Motor (Rolle × i)", "1/min"),
    "ign": ("Motor (Zündung)", "1/min"),
    "v": ("Rolle", "km/h"),
    "egt1": ("EGT 1", "°C"),
    "egt2": ("EGT 2", "°C"),
    "lam": ("Lambda", "λ"),
    "ratio": ("Übersetzung Zündung/Rolle", ""),
    "ratio_ecu": ("Übersetzung ECU/Rolle", ""),
    "rate": ("Messfrequenz", "Hz"),
}
LIVE_STANDARD = [
    {"kanal": "egt1", "min": 200.0, "max": 1000.0,
     "zonen": [[200.0, 850.0, "ok"], [850.0, 900.0, "warn"], [900.0, 1000.0, "err"]]},
    {"kanal": ECU_PREFIX + "coolant", "min": 0.0, "max": 130.0,
     "zonen": [[0.0, 60.0, "cold"], [60.0, 100.0, "ok"], [100.0, 110.0, "warn"], [110.0, 130.0, "err"]]},
    {"kanal": "lam", "min": 0.7, "max": 1.3,
     "zonen": [[0.7, 0.85, "warn"], [0.85, 0.95, "ok"], [0.95, 1.0, "warn"], [1.0, 1.3, "err"]]},
    {"kanal": "v", "min": 0.0, "max": 200.0, "zonen": []},
    {"kanal": "", "min": 0.0, "max": 100.0, "zonen": []},
]
LIVE_LEER = {"kanal": "", "min": 0.0, "max": 100.0, "zonen": []}     # Standard weiterer Plaetze (ab Platz 6)


def live_standard(i: int) -> Dict[str, Any]:
    """Standard des Live-Platzes i (0-basiert): die 5 Standardplaetze, danach leer."""
    return LIVE_STANDARD[i] if i < len(LIVE_STANDARD) else LIVE_LEER

MESSPLAETZE = 5              # Laeufe im Messfenster (gesperrte + rollierende)


def live_kanal_ok(kanal: str) -> bool:
    return kanal == "" or kanal in LIVE_KANAELE or (kanal.startswith(ECU_PREFIX) and len(kanal) > len(ECU_PREFIX))


def _live_platz(p: Any, standard: Dict[str, Any]) -> Dict[str, Any]:
    """Einen gespeicherten Live-Platz pruefen; Unbrauchbares durch den Standard ersetzen."""
    if not isinstance(p, dict):
        p = standard
    kanal = p.get("kanal", "")
    kanal = kanal if isinstance(kanal, str) and live_kanal_ok(kanal) else standard["kanal"]
    try:
        lo, hi = float(p.get("min", standard["min"])), float(p.get("max", standard["max"]))
    except (TypeError, ValueError):
        lo, hi = standard["min"], standard["max"]
    if not hi > lo:
        lo, hi = float(standard["min"]), float(standard["max"])
    zonen = []
    for z in p.get("zonen") or []:
        try:
            von, bis, stufe = float(z[0]), float(z[1]), str(z[2])
        except (TypeError, ValueError, IndexError, KeyError):
            continue
        if stufe in LIVE_STUFEN and bis > von:
            zonen.append([von, bis, stufe])
    return {"kanal": kanal, "min": lo, "max": hi, "zonen": zonen}


def messplaetze(alle_lauf_ids: List[int], gesperrt: List[int], plaetze: int = MESSPLAETZE):
    """Belegung des Messfensters: (gesperrte, rollierende).
    alle_lauf_ids: Laeufe neueste zuerst; gesperrt: Lauf-IDs mit Schloss (auch Mittelwert-IDs, muessen nicht
    in alle_lauf_ids stehen). Gesperrte bleiben (hoechstens plaetze, Reihenfolge bleibt), die uebrigen
    Plaetze fuellen die neuesten nicht gesperrten Laeufe."""
    fest = list(dict.fromkeys(gesperrt))[:max(0, plaetze)]
    frei = max(0, plaetze - len(fest))
    rollierend = [i for i in dict.fromkeys(alle_lauf_ids) if i not in fest][:frei]
    return fest, rollierend


def base_dir() -> str:
    """Datenordner ~/PyST (Datenordner von Vorversionen werden beim ersten Aufruf uebernommen)."""
    home = os.path.expanduser("~")
    new = os.path.join(home, "PyST")
    _migrate_old_dir(os.path.join(home, "SimpleDyno"), new)
    return new


def _migrate_old_dir(old: str, new: str):
    """Einmaliger Umzug alter Datenordner nach ~/PyST: Ordner und Datenbank umbenennen, gespeicherte
    absolute Pfade (Laeufe, Einstellungen) auf den neuen Ordner umschreiben."""
    if not os.path.isdir(old) or os.path.exists(new):
        return
    os.rename(old, new)
    old_db, new_db = os.path.join(new, "simpledyno.db"), os.path.join(new, "pyst.db")
    if os.path.exists(old_db) and not os.path.exists(new_db):
        os.rename(old_db, new_db)
    if os.path.exists(new_db):
        con = sqlite3.connect(new_db)
        with con:
            con.execute("UPDATE runs SET ordner = ? || substr(ordner, ?) WHERE substr(ordner, 1, ?) = ?",
                        (new, len(old) + 1, len(old), old))
        con.close()
    runs = os.path.join(new, "messungen")
    for name in (os.listdir(runs) if os.path.isdir(runs) else []):
        path = os.path.join(runs, name, "lauf.json")
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            if data.get("programm") == "SimpleDyno":
                data["programm"] = "PyST"
                with open(path, "w", encoding="utf-8") as fh:
                    json.dump(data, fh, ensure_ascii=False, indent=2)
        except (OSError, ValueError):
            pass
    settings = os.path.join(new, "einstellungen.json")
    if os.path.exists(settings):
        with open(settings, encoding="utf-8") as fh:
            text = fh.read()
        with open(settings, "w", encoding="utf-8") as fh:
            fh.write(text.replace(json.dumps(old)[1:-1], json.dumps(new)[1:-1]))


def db_file(folder: str) -> str:
    return os.path.join(folder, "pyst.db")


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
    fehler: Optional[Dict[str, float]] = None      # Fehleranteile in % (channels.fault_summary)
    # Setup-Schnappschuss [{"feld", "wert", "vorher"}] (setup_schnappschuss), leer = keiner gespeichert
    setup_werte: List[Dict[str, str]] = field(default_factory=list)
    nur_lauf: bool = False                         # Setup nur fuer diesen Lauf geaendert (nicht gespeichert)
    art: str = ART_LAUF                            # ART_LAUF | ART_DAUERMESSUNG | ART_MITTELWERT
    quellen: List[int] = field(default_factory=list)   # nur Mittelwert: Lauf-IDs der Quellen

    @property
    def path(self) -> str:
        """Datei/Ordner fuer storage.load_any() (Mittelwert: mittelwert.laden())"""
        return os.path.join(self.ordner, self.datei) if self.datei else self.ordner

    @property
    def werte(self) -> Dict[str, str]:
        """Setup-Schnappschuss als {feld: wert}"""
        return _werte_dict(self.setup_werte)

    @property
    def ist_mittelwert(self) -> bool:
        return self.art == ART_MITTELWERT


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
CREATE TABLE IF NOT EXISTS vehicle_prefs(
    vehicle_id INTEGER PRIMARY KEY REFERENCES vehicles(id) ON DELETE CASCADE, data TEXT);
"""

# Spalten, die nach dem ersten Schema dazugekommen sind (Migration per ALTER TABLE)
RUN_COLUMNS = [
    ("fehler", "TEXT"),                    # v3 (PyST 1.3): Fehleranteile je Lauf (JSON, leer = noch nicht berechnet)
    ("setup_werte", "TEXT"),               # v4: Setup-Schnappschuss (JSON-Liste)
    ("nur_lauf", "INTEGER DEFAULT 0"),     # v4: Setup nur fuer diesen Lauf geaendert
    ("art", "TEXT"),                       # v4: lauf / dauermessung / mittelwert (leer = aus quelle)
    ("quellen", "TEXT"),                   # v4: Mittelwert: Lauf-IDs der Quellen (JSON-Liste)
]
VPREF_META = "vpref:"        # globaler Standard der Fahrzeug-Ansicht: meta-Schluessel = VPREF_META + key


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
        # v3/v4: neue Spalten in runs (alte Zeilen bekommen NULL bzw. den Standard)
        cols = {r["name"] for r in self.con.execute("PRAGMA table_info(runs)")}
        for name, typ in RUN_COLUMNS:
            if name not in cols:
                self.con.execute(f"ALTER TABLE runs ADD COLUMN {name} {typ}")
        self.con.commit()

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
                      sichtbar=bool(r["sichtbar"]), params=json.loads(r["params"] or "{}"),
                      fehler=json.loads(r["fehler"]) if r["fehler"] else None,
                      setup_werte=_json_list(r["setup_werte"]), nur_lauf=bool(r["nur_lauf"]),
                      art=r["art"] or art_aus_quelle(r["quelle"] or ""),
                      quellen=[int(i) for i in _json_list(r["quellen"])])

    def runs(self, vehicle_id: Optional[int] = None, setup_id: Optional[int] = None,
             mittelwert: bool = False) -> List[RunRow]:
        """Laeufe neueste zuerst. Mittelwert-Laeufe (Ordner ohne Rohdaten, storage.load_any geht dort nicht) nur
        mit mittelwert=True."""
        q, cond, args = "SELECT * FROM runs", [], []
        if setup_id:
            cond, args = ["setup_id=?"], [setup_id]
        elif vehicle_id:
            cond, args = ["vehicle_id=?"], [vehicle_id]
        if not mittelwert:
            cond.append("COALESCE(art, '') != ? AND COALESCE(quelle, '') != ?")
            args += [ART_MITTELWERT, QUELLE_MITTELWERT]
        if cond:
            q += " WHERE " + " AND ".join(cond)
        return [self._row(r) for r in self.con.execute(q + " ORDER BY datum DESC", args)]

    def run(self, rid: int) -> Optional[RunRow]:
        r = self.con.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone()
        return None if r is None else self._row(r)

    def add_run(self, ordner: str, datei: str, quelle: str, name: str, datum: str, result=None,
                params: Optional[Dict[str, Any]] = None, rate: float = 0.0, vehicle_id=None, setup_id=None,
                notiz: str = "", farbe: str = "", sichtbar: bool = True,
                setup_werte: Optional[List[Dict[str, str]]] = None, nur_lauf: bool = False, art: str = "",
                quellen: Optional[List[int]] = None) -> int:
        """art leer = aus quelle ("Dauermessung" -> dauermessung, sonst lauf)."""
        vals = (0.0, 0.0, 0.0, 0.0, 0.0) if result is None else \
            (result.p_max, result.n_pmax, result.m_max, result.n_mmax, result.ka)
        art = art or art_aus_quelle(quelle)
        if art not in ARTEN:
            raise ValueError(f"unbekannte Art des Laufs: {art}")
        rid = self.con.execute(
            "INSERT INTO runs(datum, name, quelle, ordner, datei, vehicle_id, setup_id, p_max, n_pmax, m_max,"
            " n_mmax, ka, rate, params, notiz, farbe, sichtbar, setup_werte, nur_lauf, art, quellen)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (datum, name, quelle, ordner, datei, vehicle_id, setup_id, *vals, rate,
             json.dumps(params or {}), notiz, farbe, int(sichtbar),
             json.dumps(list(setup_werte or []), ensure_ascii=False), int(bool(nur_lauf)), art,
             json.dumps([int(i) for i in quellen or []]))).lastrowid
        self.con.commit()
        return rid

    def update_run(self, rid: int, **kw):
        if not kw:
            return
        if "params" in kw:
            kw["params"] = json.dumps(kw["params"])
        if "sichtbar" in kw:
            kw["sichtbar"] = int(kw["sichtbar"])
        if "nur_lauf" in kw:
            kw["nur_lauf"] = int(bool(kw["nur_lauf"]))
        if "fehler" in kw and kw["fehler"] is not None:
            kw["fehler"] = json.dumps(kw["fehler"])
        if "setup_werte" in kw:
            kw["setup_werte"] = json.dumps(list(kw["setup_werte"] or []), ensure_ascii=False)
        if "quellen" in kw:
            kw["quellen"] = json.dumps([int(i) for i in kw["quellen"] or []])
        if "art" in kw and kw["art"] not in ARTEN:
            raise ValueError(f"unbekannte Art des Laufs: {kw['art']}")
        cols = ", ".join(f"{k}=?" for k in kw)
        self.con.execute(f"UPDATE runs SET {cols} WHERE id=?", (*kw.values(), rid))
        self.con.commit()

    def lauf_setup(self, row: RunRow) -> Dict[str, Any]:
        """Setup, mit dem der Lauf gefahren wurde: gespeichertes Setup, ueberschrieben vom Schnappschuss des
        Laufs (nur_lauf-Aenderungen). {} ohne beides. Zum Vergleichen (mittelwert.setup_abweichungen)."""
        s = dict(self.setup(row.setup_id) or {}) if row.setup_id else {}
        s.update(row.werte)
        return s

    def delete_run(self, rid: int, delete_files: bool = False):
        r = self.run(rid)
        self.con.execute("DELETE FROM runs WHERE id=?", (rid,))
        self.con.commit()
        if delete_files and r and r.ordner.startswith(self.runs_dir()) and os.path.isdir(r.ordner):
            shutil.rmtree(r.ordner, ignore_errors=True)

    # ------------------------------------------------------------ Import
    def _target(self, name: str) -> str:
        base = name.strip().replace(" ", "_") or "lauf"
        target = os.path.join(self.runs_dir(), base)
        n = 1
        while os.path.exists(target):
            n += 1
            target = os.path.join(self.runs_dir(), f"{base}_{n}")
        return target

    @staticmethod
    def _iso(datum: str) -> str:
        for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%Y %H:%M:%S"):
            try:
                return _dt.datetime.strptime(datum.strip(), fmt).isoformat(timespec="seconds")
            except ValueError:
                pass
        return datum

    def _add_loaded(self, run, target: str, datei: str, name: str, vehicle_id=None, setup_id=None,
                    notiz: str = "", farbe: str = "", params=None, sichtbar: bool = False) -> int:
        from . import physics, storage
        from .channels import run_faults
        p = params or run["params"]
        res = None if run.get("kind") == "dauermessung" else storage.recompute(run, p)
        datum = self._iso(run.get("date", "") or self._now())
        try:
            fehler = run_faults(run)[1]
        except Exception:
            fehler = None
        quelle = "Dauermessung" if run.get("kind") == "dauermessung" else run["source"]
        rid = self.add_run(target, datei, quelle, name, datum, res, params=p.__dict__,
                           rate=physics.sample_rate(run["dt"]), vehicle_id=vehicle_id, setup_id=setup_id,
                           notiz=notiz, farbe=farbe, sichtbar=sichtbar)
        if fehler is not None:
            self.update_run(rid, fehler=fehler)
        return rid

    def import_file(self, path: str, vehicle_id=None, setup_id=None, params=None) -> int:
        """LabVIEW-XML, PyST-Ordner oder Terminal-Mitschnitt in die Datenbank uebernehmen (wird kopiert bzw.
        umgewandelt). params: Pruefstandsparameter fuer Mitschnitte (die keine enthalten).
        .pyst-Dateien: import_pyst (mehrere Laeufe)."""
        from . import storage
        if path.lower().endswith(storage.PYST_EXT):
            return self.import_pyst(path, vehicle_id, setup_id)[0]
        if storage.is_terminal_log(path):
            run = storage.parse_terminal_log(path, params)
            target = self._target(run["name"])
            storage.write_imported_log(run, target)
            return self._add_loaded(run, target, "lauf.json", run["name"], vehicle_id, setup_id,
                                    notiz=run["meta"].get("hinweis", ""))
        run = storage.load_any(path)
        src = path if not os.path.isdir(path) else os.path.join(path, "lauf.json")
        name = os.path.splitext(os.path.basename(src))[0] if run["source"] == "LabVIEW" else run["name"]
        target = self._target(name)
        if os.path.isdir(path):
            shutil.copytree(path, target)
            datei = "lauf.json"
        elif path.lower().endswith(".json"):
            shutil.copytree(os.path.dirname(os.path.abspath(path)), target)
            datei = "lauf.json"
        else:
            os.makedirs(target)
            datei = os.path.basename(path)
            shutil.copy2(path, os.path.join(target, datei))
        notiz = " ".join(x for x in (run.get("vehicle", ""), run.get("title", "")) if x)
        return self._add_loaded(run, target, datei, name, vehicle_id, setup_id, notiz=notiz)

    def add_folder(self, folder: str, vehicle_id=None, setup_id=None, sichtbar: bool = False) -> int:
        """Bereits im Datenordner liegenden Lauf (z.B. beendete Dauermessung) eintragen."""
        from . import storage
        run = storage.load_any(folder)
        return self._add_loaded(run, folder, "lauf.json", run["name"], vehicle_id, setup_id, sichtbar=sichtbar)

    def _find_vehicle(self, data: Dict[str, Any]) -> Optional[int]:
        name = self.vehicle_name(data)
        for r in self.vehicles():
            if r["name"] == name:
                return r["id"]
        return None

    def import_pyst(self, path: str, vehicle_id=None, setup_id=None) -> List[int]:
        """Alle Laeufe einer .pyst-Datei uebernehmen. Fahrzeug/Setup aus der Datei werden angelegt (bzw. ein
        gleichnamiges verwendet), sonst gelten vehicle_id/setup_id. Schon vorhandene Laeufe (gleicher Name,
        Datum, Quelle) werden nicht doppelt angelegt."""
        from . import physics, storage
        ids = []
        for run in storage.load_pyst(path):
            e = run["pyst"]
            datum = self._iso(e.get("datum") or run.get("date", ""))
            quelle = e.get("quelle") or run["source"]
            dup = self.con.execute("SELECT id FROM runs WHERE name=? AND datum=? AND quelle=?",
                                   (e.get("name", run["name"]), datum, quelle)).fetchone()
            if dup:
                ids.append(dup["id"])
                continue
            vid, sid = vehicle_id, setup_id
            veh = e.get("fahrzeug") or {}
            if veh:
                vdata = {k: v for k, v in veh.items() if k not in ("id", "notiz")}
                vid = self._find_vehicle(vdata) or self.save_vehicle({**vdata, "notiz": veh.get("notiz", "")})
                sid = None
                st = e.get("setup") or {}
                if st:
                    for r in self.setups(vid):
                        if (r["name"] or "") == st.get("name", ""):
                            sid = r["id"]
                            break
                    if sid is None:
                        sdata = {k: v for k, v in st.items() if k not in ("id", "vehicle_id", "name", "notiz")}
                        sid = self.save_setup(vid, st.get("name", ""), sdata, st.get("notiz", ""))
            target = self._target(e.get("name") or run["name"])
            os.makedirs(target)
            storage.extract_pyst_entry(path, e, target)
            datei = "lauf.json" if os.path.exists(os.path.join(target, "lauf.json")) else \
                next((f for f in os.listdir(target) if f.lower().endswith(".xml")), "lauf.json")
            p = run["params"]
            res = None if run.get("kind") == "dauermessung" else storage.recompute(run, p)
            werte = e.get("setup_werte")
            werte = [w for w in werte if isinstance(w, dict) and w.get("feld")] if isinstance(werte, list) else []
            rid = self.add_run(target, datei, quelle, e.get("name", run["name"]), datum, res, params=p.__dict__,
                               rate=physics.sample_rate(run["dt"]), vehicle_id=vid, setup_id=sid,
                               notiz=e.get("notiz", ""), farbe=e.get("farbe", ""), sichtbar=False,
                               setup_werte=werte, nur_lauf=bool(e.get("nur_lauf", False)))
            if e.get("fehler"):
                self.update_run(rid, fehler=e["fehler"])
            ids.append(rid)
        return ids

    # ------------------------------------------------------------ Einstellungen
    def get_meta(self, key: str, default: str = "") -> str:
        r = self.con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default

    def set_meta(self, key: str, value: str):
        self.con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?,?)", (key, value))
        self.con.commit()

    # ------------------------------------------------------------ Ansicht je Fahrzeug
    def _vprefs(self, vid: int) -> Dict[str, Any]:
        r = self.con.execute("SELECT data FROM vehicle_prefs WHERE vehicle_id=?", (vid,)).fetchone()
        try:
            d = json.loads(r["data"] or "{}") if r else {}
        except ValueError:
            d = {}
        return d if isinstance(d, dict) else {}

    def vehicle_pref(self, vid: Optional[int], key: str, default: Any = None) -> Any:
        """Gespeicherte Einstellung je Fahrzeug (JSON-Wert). Hat das Fahrzeug keine, gilt der globale Standard
        (vid None, in meta), sonst default."""
        if vid:
            d = self._vprefs(vid)
            if key in d:
                return d[key]
        raw = self.get_meta(VPREF_META + key)
        if raw:
            try:
                return json.loads(raw)
            except ValueError:
                pass
        return default

    def set_vehicle_pref(self, vid: Optional[int], key: str, value: Any) -> bool:
        """Einstellung je Fahrzeug (vid None = globaler Standard) speichern; value None loescht sie (dann gilt
        wieder der globale Standard). False, wenn es das Fahrzeug nicht (mehr) gibt."""
        if not vid:
            if value is None:
                self.con.execute("DELETE FROM meta WHERE key=?", (VPREF_META + key,))
                self.con.commit()
            else:
                self.set_meta(VPREF_META + key, json.dumps(value, ensure_ascii=False))
            return True
        if self.con.execute("SELECT 1 FROM vehicles WHERE id=?", (vid,)).fetchone() is None:
            return False
        d = self._vprefs(vid)
        if value is None:
            d.pop(key, None)
        else:
            d[key] = value
        self.con.execute("INSERT OR REPLACE INTO vehicle_prefs(vehicle_id, data) VALUES(?,?)",
                         (vid, json.dumps(d, ensure_ascii=False)))
        self.con.commit()
        return True

    def schnellwert_plaetze(self, vid: Optional[int]) -> List[str]:
        """Setup-Felder der Schnellwert-Zeile (Messen) und der Laufleiste (Auswertung); "" = Platz leer.
        Beliebig viele (0 … SCHNELLWERT_MAX) je Fahrzeug; gespeicherte Listen aelterer Versionen (3 Plaetze) gelten
        weiter, ohne Eintrag die 3 Standardfelder."""
        keys = self.vehicle_pref(vid, "schnellwerte", None)
        if not isinstance(keys, list):
            keys = SCHNELLWERT_STANDARD
        known = set(all_setup_keys())
        return [k if isinstance(k, str) and k in known else "" for k in keys][:SCHNELLWERT_MAX]

    def set_schnellwert_plaetze(self, vid: Optional[int], keys: List[str]) -> bool:
        known = set(all_setup_keys())
        keys = [k if isinstance(k, str) and k in known else "" for k in list(keys)[:SCHNELLWERT_MAX]]
        return self.set_vehicle_pref(vid, "schnellwerte", keys)

    def live_plaetze(self, vid: Optional[int]) -> List[Dict[str, Any]]:
        """Die Live-Plaetze (Messen), 0 … LIVE_MAX je Fahrzeug: [{"kanal", "min", "max", "zonen": [[von, bis, stufe],
        ...]}], kanal: Schluessel aus LIVE_KANAELE, ECU_PREFIX + rusEFI-Kanal oder "" (leer); stufe aus LIVE_STUFEN.
        Ohne Eintrag die 5 Standardplaetze; gespeicherte Listen aelterer Versionen (immer 5 Plaetze) gelten weiter."""
        raw = self.vehicle_pref(vid, "live", None)
        if not isinstance(raw, list):
            raw = LIVE_STANDARD
        return [_live_platz(x, live_standard(i)) for i, x in enumerate(raw[:LIVE_MAX])]

    def set_live_plaetze(self, vid: Optional[int], plaetze: List[Dict[str, Any]]) -> bool:
        """Plaetze speichern (so viele wie angegeben, hoechstens LIVE_MAX)."""
        p = list(plaetze)[:LIVE_MAX]
        return self.set_vehicle_pref(vid, "live", [_live_platz(x, live_standard(i)) for i, x in enumerate(p)])

    def gesperrte_plaetze(self, vid: Optional[int]) -> List[int]:
        """Lauf-IDs mit Schloss im Messfenster (auch Mittelwerte), hoechstens MESSPLAETZE, Reihenfolge wie
        gespeichert. Geloeschte Laeufe fallen heraus."""
        ids = self.vehicle_pref(vid, "gesperrt", [])
        out: List[int] = []
        for i in ids if isinstance(ids, list) else []:
            try:
                i = int(i)
            except (TypeError, ValueError):
                continue
            if i not in out and self.con.execute("SELECT 1 FROM runs WHERE id=?", (i,)).fetchone():
                out.append(i)
        return out[:MESSPLAETZE]

    def set_gesperrte_plaetze(self, vid: Optional[int], ids: List[int]) -> bool:
        return self.set_vehicle_pref(vid, "gesperrt", list(dict.fromkeys(int(i) for i in ids))[:MESSPLAETZE])


def _json_list(text) -> list:
    """JSON-Liste aus einer Spalte (NULL, leer oder kaputt -> [])"""
    if not text:
        return []
    try:
        v = json.loads(text)
    except ValueError:
        return []
    return v if isinstance(v, list) else []
