"""Datenmodell fuer den Oberflaechen-Umbau (pyst.db): Migration, Setup-Schnappschuss, Ansicht je Fahrzeug,
Messplaetze, .pyst-Austausch der Setup-Werte."""
import glob
import json
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pyst import db as dbm, storage  # noqa: E402
from pyst.db import Database  # noqa: E402

DATA = os.path.join(os.path.dirname(__file__), "data")

# Schema vor PyST 1.3 (ohne fehler) und ohne die neuen Spalten/Tabelle
OLD_SCHEMA = """
CREATE TABLE vehicles(id INTEGER PRIMARY KEY, name TEXT, data TEXT, notiz TEXT, erstellt TEXT);
CREATE TABLE setups(id INTEGER PRIMARY KEY, vehicle_id INTEGER REFERENCES vehicles(id) ON DELETE CASCADE,
    name TEXT, data TEXT, notiz TEXT, erstellt TEXT);
CREATE TABLE runs(id INTEGER PRIMARY KEY, datum TEXT, name TEXT, quelle TEXT, ordner TEXT, datei TEXT,
    vehicle_id INTEGER REFERENCES vehicles(id) ON DELETE SET NULL,
    setup_id INTEGER REFERENCES setups(id) ON DELETE SET NULL,
    p_max REAL, n_pmax REAL, m_max REAL, n_mmax REAL, ka REAL, rate REAL,
    params TEXT, notiz TEXT, farbe TEXT, sichtbar INTEGER DEFAULT 0);
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
"""


def old_db(path):
    con = sqlite3.connect(path)
    con.executescript(OLD_SCHEMA)
    con.execute("INSERT INTO vehicles(id, name, data, notiz, erstellt) VALUES(1, 'Aprilia RS', ?, '', '')",
                (json.dumps({"hersteller": "Aprilia", "modell": "RS"}),))
    con.execute("INSERT INTO setups(id, vehicle_id, name, data, notiz, erstellt) VALUES(1, 1, 'Serie', ?, '', '')",
                (json.dumps({"hauptduese": "135", "zzp": "18"}),))
    for i, (q, d) in enumerate((("LabVIEW", "2026-05-01T10:00:00"), ("Dauermessung", "2026-05-02T10:00:00"),
                                ("PyST", "2026-05-03T10:00:00")), 1):
        con.execute("INSERT INTO runs(id, datum, name, quelle, ordner, datei, vehicle_id, setup_id, p_max, params,"
                    " sichtbar) VALUES(?,?,?,?,?,?,1,1,?,?,1)", (i, d, f"Lauf {i}", q, "/x", "", 10.0 + i, "{}"))
    con.commit()
    con.close()


class Migration(unittest.TestCase):
    def test_alte_datenbank(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "pyst.db")
            old_db(path)
            db = Database(path)
            cols = {r["name"] for r in db.con.execute("PRAGMA table_info(runs)")}
            self.assertTrue({"fehler", "setup_werte", "nur_lauf", "art", "quellen"} <= cols)
            tabs = {r["name"] for r in db.con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertIn("vehicle_prefs", tabs)
            rows = db.runs()
            self.assertEqual([r.id for r in rows], [3, 2, 1])
            for r in rows:
                self.assertEqual(r.setup_werte, [])
                self.assertFalse(r.nur_lauf)
                self.assertEqual(r.quellen, [])
                self.assertIsNone(r.fehler)
                self.assertEqual(r.werte, {})
            self.assertEqual({r.id: r.art for r in rows}, {1: "lauf", 2: "dauermessung", 3: "lauf"})
            self.assertEqual(db.run(1).p_max, 11.0)
            # Ansicht: Standardwerte ohne gespeicherte Einstellungen
            self.assertEqual(db.schnellwert_plaetze(1), ["hauptduese", "clip", "zzp"])
            self.assertEqual(len(db.live_plaetze(1)), 5)
            self.assertEqual(db.gesperrte_plaetze(1), [])
            db.con.close()
            # zweites Oeffnen: Migration laeuft nicht doppelt
            db = Database(path)
            db.update_run(1, setup_werte=[{"feld": "zzp", "wert": "17", "vorher": "18"}], nur_lauf=True)
            self.assertEqual(db.run(1).werte, {"zzp": "17"})
            db.con.close()

    def test_neue_datenbank(self):
        with tempfile.TemporaryDirectory() as d:
            db = Database(os.path.join(d, "pyst.db"))
            rid = db.add_run("/x", "", "PyST", "a", "2026-11-08T10:00:00")
            r = db.run(rid)
            self.assertEqual((r.art, r.setup_werte, r.nur_lauf, r.quellen), ("lauf", [], False, []))
            rid = db.add_run("/x", "", "Dauermessung", "b", "2026-11-08T10:01:00")
            self.assertEqual(db.run(rid).art, "dauermessung")
            with self.assertRaises(ValueError):
                db.add_run("/x", "", "PyST", "c", "2026-11-08T10:02:00", art="quatsch")
            with self.assertRaises(ValueError):
                db.update_run(rid, art="quatsch")


class Schnappschuss(unittest.TestCase):
    def test_setup_schnappschuss(self):
        setup = {"id": 3, "name": "Serie", "hauptduese": "135", "clip": "3", "zzp": 18.0}
        s = dbm.setup_schnappschuss(setup, ["hauptduese", "clip", "zzp", "", "nadel"])
        self.assertEqual(s, [{"feld": "hauptduese", "wert": "135", "vorher": ""},
                             {"feld": "clip", "wert": "3", "vorher": ""},
                             {"feld": "zzp", "wert": "18", "vorher": ""},
                             {"feld": "nadel", "wert": "", "vorher": ""}])
        s = dbm.setup_schnappschuss(setup, ["hauptduese", "clip", "zzp"], {"hauptduese": "138", "zzp": "18"})
        self.assertEqual(s[0], {"feld": "hauptduese", "wert": "138", "vorher": "135"})
        self.assertEqual(s[2], {"feld": "zzp", "wert": "18", "vorher": ""})      # gleich -> kein vorher
        s = dbm.setup_schnappschuss(None, ["hauptduese"], {"hauptduese": 140})
        self.assertEqual(s, [{"feld": "hauptduese", "wert": "140", "vorher": ""}])  # unbekannt -> leer

    def test_speichern_lesen(self):
        with tempfile.TemporaryDirectory() as d:
            db = Database(os.path.join(d, "pyst.db"))
            vid = db.save_vehicle({"hersteller": "Aprilia", "modell": "RS"})
            sid = db.save_setup(vid, "Serie", {"hauptduese": "135", "clip": "3", "zzp": "18"})
            werte = dbm.setup_schnappschuss(db.setup(sid), ["hauptduese", "clip"], {"hauptduese": "138"})
            rid = db.add_run("/x", "", "PyST", "a", "2026-11-08T10:00:00", vehicle_id=vid, setup_id=sid,
                             setup_werte=werte, nur_lauf=True)
            r = db.run(rid)
            self.assertEqual(r.setup_werte, werte)
            self.assertTrue(r.nur_lauf)
            self.assertEqual(r.werte, {"hauptduese": "138", "clip": "3"})
            ls = db.lauf_setup(r)
            self.assertEqual((ls["hauptduese"], ls["zzp"], ls["name"]), ("138", "18", "Serie"))
            db.update_run(rid, setup_werte=[], nur_lauf=False)
            r = db.run(rid)
            self.assertEqual((r.setup_werte, r.nur_lauf), ([], False))
            self.assertEqual(db.lauf_setup(r)["hauptduese"], "135")
            self.assertEqual(db.lauf_setup(db.run(db.add_run("/x", "", "PyST", "b", "2026-11-08T10:05:00"))), {})

    def test_schnellwert_abweichungen(self):
        alt = [{"feld": "hauptduese", "wert": "135", "vorher": ""}, {"feld": "clip", "wert": "3", "vorher": ""},
               {"feld": "zzp", "wert": "18", "vorher": ""}]
        neu = [{"feld": "hauptduese", "wert": "140", "vorher": "135"}, {"feld": "clip", "wert": "2", "vorher": ""},
               {"feld": "zzp", "wert": "18", "vorher": ""}, {"feld": "nadel", "wert": "J8", "vorher": ""}]
        self.assertEqual(dbm.schnellwert_abweichungen(alt, neu), {"hauptduese": "135", "clip": "3"})
        self.assertEqual(dbm.schnellwert_abweichungen({"zzp": 18.0}, {"zzp": "18"}), {})
        self.assertEqual(dbm.schnellwert_abweichungen(None, neu), {})
        self.assertEqual(dbm.schnellwert_abweichungen([], []), {})
        self.assertEqual(dbm.schnellwert_pfeil("140", "135"), "↑")
        self.assertEqual(dbm.schnellwert_pfeil("2", "3"), "↓")
        self.assertEqual(dbm.schnellwert_pfeil("2,5", "2.25"), "↑")
        self.assertEqual(dbm.schnellwert_pfeil("J8", "J6"), "→")
        self.assertEqual(dbm.schnellwert_pfeil("18", 18.0), "")


class AnsichtJeFahrzeug(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(os.path.join(self.tmp.name, "pyst.db"))
        self.v1 = self.db.save_vehicle({"hersteller": "Aprilia", "modell": "RS 125"})
        self.v2 = self.db.save_vehicle({"hersteller": "Yamaha", "modell": "DT"})

    def tearDown(self):
        self.db.con.close()
        self.tmp.cleanup()

    def test_vehicle_pref(self):
        db = self.db
        self.assertEqual(db.vehicle_pref(self.v1, "x", 5), 5)
        self.assertTrue(db.set_vehicle_pref(None, "x", {"a": 1}))
        self.assertEqual(db.vehicle_pref(self.v1, "x", 5), {"a": 1})     # globaler Standard
        self.assertEqual(db.vehicle_pref(None, "x", 5), {"a": 1})
        self.assertTrue(db.set_vehicle_pref(self.v1, "x", [1, "ä"]))
        self.assertEqual(db.vehicle_pref(self.v1, "x"), [1, "ä"])
        self.assertEqual(db.vehicle_pref(self.v2, "x"), {"a": 1})
        db.set_vehicle_pref(self.v1, "y", 3)
        self.assertEqual((db.vehicle_pref(self.v1, "x"), db.vehicle_pref(self.v1, "y")), ([1, "ä"], 3))
        db.set_vehicle_pref(self.v1, "x", None)                          # loeschen -> wieder global
        self.assertEqual(db.vehicle_pref(self.v1, "x"), {"a": 1})
        db.set_vehicle_pref(None, "x", None)
        self.assertIsNone(db.vehicle_pref(self.v1, "x"))
        self.assertFalse(db.set_vehicle_pref(999, "x", 1))               # Fahrzeug gibt es nicht

    def test_schnellwert_plaetze(self):
        db = self.db
        self.assertEqual(db.schnellwert_plaetze(self.v1), dbm.SCHNELLWERT_STANDARD)
        self.assertEqual(db.schnellwert_plaetze(None), ["hauptduese", "clip", "zzp"])
        db.set_schnellwert_plaetze(self.v1, ["vario", "", "gibtsnicht", "zzp"])
        self.assertEqual(db.schnellwert_plaetze(self.v1), ["vario", "", ""])
        self.assertEqual(db.schnellwert_plaetze(self.v2), dbm.SCHNELLWERT_STANDARD)
        db.set_schnellwert_plaetze(None, ["rollen"])
        self.assertEqual(db.schnellwert_plaetze(self.v2), ["rollen", "", ""])
        self.assertEqual(db.schnellwert_plaetze(self.v1), ["vario", "", ""])

    def test_live_plaetze(self):
        db = self.db
        p = db.live_plaetze(self.v1)
        self.assertEqual(len(p), dbm.LIVE_ANZAHL)
        self.assertEqual([x["kanal"] for x in p], ["egt1", "ecu:coolant", "lam", "v", ""])
        self.assertEqual((p[0]["min"], p[0]["max"]), (200.0, 1000.0))
        self.assertIn([850.0, 900.0, "warn"], p[0]["zonen"])
        self.assertIn([900.0, 1000.0, "err"], p[0]["zonen"])
        self.assertIn([0.85, 0.95, "ok"], p[2]["zonen"])
        self.assertEqual((p[3]["min"], p[3]["max"]), (0.0, 200.0))
        for x in p:
            self.assertTrue(dbm.live_kanal_ok(x["kanal"]))
            for z in x["zonen"]:
                self.assertIn(z[2], dbm.LIVE_STUFEN)
        neu = [{"kanal": "ecu:MAPValue", "min": 0, "max": 120, "zonen": [[0, 100, "ok"], [100, 120, "err"],
                                                                         [5, 1, "ok"], [1, 2, "bunt"]]},
               {"kanal": "gibtsnicht", "min": 5, "max": 1},
               {"kanal": "egt2", "min": 300, "max": 900, "zonen": []}]
        db.set_live_plaetze(self.v1, neu)
        p = db.live_plaetze(self.v1)
        self.assertEqual(p[0], {"kanal": "ecu:MAPValue", "min": 0.0, "max": 120.0,
                                "zonen": [[0.0, 100.0, "ok"], [100.0, 120.0, "err"]]})
        self.assertEqual(p[1]["kanal"], "ecu:coolant")                    # unbekannt -> Standard des Platzes
        self.assertEqual((p[1]["min"], p[1]["max"]), (0.0, 130.0))
        self.assertEqual(p[2]["kanal"], "egt2")
        self.assertEqual(p[3]["kanal"], "v")                              # nicht angegeben -> Standard
        self.assertEqual(db.live_plaetze(self.v2)[0]["kanal"], "egt1")
        db.set_live_plaetze(None, [{"kanal": "n", "min": 0, "max": 12000, "zonen": []}])
        self.assertEqual(db.live_plaetze(self.v2)[0]["kanal"], "n")
        self.assertEqual(db.live_plaetze(self.v1)[0]["kanal"], "ecu:MAPValue")
        # Standardliste wird beim Lesen nicht veraendert
        db.live_plaetze(None)[0]["zonen"].append([0, 1, "ok"])
        self.assertEqual(dbm.LIVE_STANDARD[0]["zonen"][0], [200.0, 850.0, "ok"])

    def test_gesperrte_plaetze_und_aufraeumen(self):
        db = self.db
        ids = [db.add_run("/x", "", "PyST", f"l{i}", f"2026-11-08T10:0{i}:00", vehicle_id=self.v1)
               for i in range(8)]
        self.assertEqual(db.gesperrte_plaetze(self.v1), [])
        db.set_gesperrte_plaetze(self.v1, [ids[5], ids[1], ids[5], ids[2], ids[3], ids[4], ids[6]])
        self.assertEqual(db.gesperrte_plaetze(self.v1), [ids[5], ids[1], ids[2], ids[3], ids[4]])
        db.delete_run(ids[1])
        self.assertEqual(db.gesperrte_plaetze(self.v1), [ids[5], ids[2], ids[3], ids[4]])
        self.assertEqual(db.gesperrte_plaetze(self.v2), [])
        db.set_gesperrte_plaetze(None, [ids[0]])
        self.assertEqual(db.gesperrte_plaetze(self.v2), [ids[0]])
        self.assertEqual(db.gesperrte_plaetze(None), [ids[0]])
        # Fahrzeug loeschen raeumt seine Ansicht weg
        db.set_schnellwert_plaetze(self.v1, ["vario", "", ""])
        n = db.con.execute("SELECT COUNT(*) FROM vehicle_prefs WHERE vehicle_id=?", (self.v1,)).fetchone()[0]
        self.assertEqual(n, 1)
        db.delete_vehicle(self.v1)
        n = db.con.execute("SELECT COUNT(*) FROM vehicle_prefs WHERE vehicle_id=?", (self.v1,)).fetchone()[0]
        self.assertEqual(n, 0)
        self.assertEqual(db.vehicle_pref(None, "gesperrt"), [ids[0]])   # globaler Standard bleibt

    def test_messplaetze(self):
        alle = [10, 9, 8, 7, 6, 5, 4]                                    # neueste zuerst
        self.assertEqual(dbm.messplaetze(alle, []), ([], [10, 9, 8, 7, 6]))
        self.assertEqual(dbm.messplaetze(alle, [7]), ([7], [10, 9, 8, 6]))
        self.assertEqual(dbm.messplaetze(alle, [4, 99]), ([4, 99], [10, 9, 8]))   # 99 z.B. ein Mittelwert
        self.assertEqual(dbm.messplaetze(alle, [1, 2, 3, 4, 5, 6, 2]), ([1, 2, 3, 4, 5], []))
        self.assertEqual(dbm.messplaetze([3, 2], [2]), ([2], [3]))
        self.assertEqual(dbm.messplaetze([], []), ([], []))
        self.assertEqual(dbm.messplaetze(alle, [7], plaetze=3), ([7], [10, 9]))
        # neuer Lauf schiebt den aeltesten nicht gesperrten heraus
        self.assertEqual(dbm.messplaetze([11] + alle, [7]), ([7], [11, 10, 9, 8]))


class PystAustausch(unittest.TestCase):
    def test_import_mit_und_ohne_setup_werte(self):
        with tempfile.TemporaryDirectory() as d:
            db = Database(os.path.join(d, "quelle", "pyst.db"))
            vid = db.save_vehicle({"hersteller": "Aprilia", "modell": "SR2"})
            sid = db.save_setup(vid, "HD 128", {"hauptduese": "128"})
            files = sorted(glob.glob(os.path.join(DATA, "*.xml")))[:2]
            ids = [db.import_file(f, vehicle_id=vid, setup_id=sid) for f in files]
            werte = [{"feld": "hauptduese", "wert": "130", "vorher": "128"}]
            items = []
            for k, rid in enumerate(ids):
                r = db.run(rid)
                it = {"ordner": r.ordner, "name": r.name, "datum": r.datum, "quelle": r.quelle, "art": r.art,
                      "fahrzeug": db.vehicle(r.vehicle_id), "setup": db.setup(r.setup_id), "notiz": "",
                      "farbe": "", "parameter": r.params, "fehler": r.fehler}
                if k == 0:
                    it.update(setup_werte=werte, nur_lauf=True)
                items.append(it)
            path = storage.export_pyst(os.path.join(d, "kunde"), items)
            kd = Database(os.path.join(d, "kunde", "pyst.db"))
            new = kd.import_pyst(path)
            a, b = kd.run(new[0]), kd.run(new[1])
            self.assertEqual(a.setup_werte, werte)
            self.assertTrue(a.nur_lauf)
            self.assertEqual(kd.lauf_setup(a)["hauptduese"], "130")
            self.assertEqual((b.setup_werte, b.nur_lauf), ([], False))
            self.assertEqual(kd.lauf_setup(b)["hauptduese"], "128")
            self.assertEqual(kd.import_pyst(path), new)                   # nicht doppelt
            kd.con.close()
            db.con.close()


if __name__ == "__main__":
    unittest.main()
