"""Stufe 2, Reiter Messen: Zustand neben START, Setup-Werte-Zeile mit Nachfrage vor START und Schnappschuss,
Live-Plaetze je Fahrzeug, Schloss/Plaetze der Laufliste, Mittelwert als Referenz mit Band, Band im DynoPlot."""
import json
import os
import sys
import time
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets  # noqa: E402

from pyst import messen_page as mp, mittelwert, theme  # noqa: E402
from pyst.plots import DynoPlot  # noqa: E402

APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
DATA = os.path.join(os.path.dirname(__file__), "data")


def pump(sec: float = 0.0):
    end = time.time() + sec
    while True:
        APP.processEvents()
        if time.time() >= end:
            return
        time.sleep(0.01)


# ============================================================================================== Bausteine
class Bausteine(unittest.TestCase):
    def test_setup_zeile_aenderung_marke_name(self):
        z = mp.SetupZeile()
        z.set_setup(5, "Serie, HD 135, Auspuff Arrow", {"hauptduese": "135", "clip": "3"},
                    ["hauptduese", "clip", "zzp"], "Aprilia RS 125", "Vergaser")
        self.assertFalse(z.ist_geaendert())
        self.assertEqual(z.marke_text(), "")
        z.set_wert("hauptduese", "138")
        self.assertEqual(z.aenderungen(), {"hauptduese": "138"})
        self.assertIn("HD 135 → HD 138", z.marke_text())
        self.assertIn("geändert, ungespeichert", z.marke_text())
        self.assertEqual(z.neuer_name(), "Serie, HD 138, Auspuff Arrow")
        z.set_nur_lauf(True)
        self.assertIn("nur für diesen Lauf", z.marke_text())
        z.set_wert("hauptduese", "135")                 # zurueck auf den Setup-Wert: keine Aenderung mehr
        self.assertFalse(z.ist_geaendert())
        z.set_wert("clip", "4")
        z.verwerfen()
        self.assertFalse(z.ist_geaendert())
        self.assertEqual(z.wert("clip"), "3")

    def test_hilfsfunktionen(self):
        self.assertEqual(mp.tick_dez(0, 200), 0)
        self.assertEqual(mp.tick_dez(0.7, 1.3), 1)
        self.assertEqual(mp.ecu_titel("coolant"), ("CLT", "°C"))
        self.assertEqual(mp.ecu_titel("coolant", "Kühlmittel", "°C"), ("Kühlmittel", "°C"))
        self.assertEqual(mp.ecu_titel("xyz"), ("xyz", ""))
        self.assertEqual(mp.alter_text(40), "vor 40 s")
        self.assertEqual(mp.alter_text(185), "vor 3 min")

    def test_band_im_diagramm(self):
        p = DynoPlot()
        n = np.linspace(3000, 11000, 50)
        p.set_curves("1", n, n / 400, n / 500)
        self.assertNotIn("1", p.bands)                    # ohne Band wie bisher
        p.set_band("1", n, {"ps": (n / 400 - 1, n / 400 + 1), "nm": (n / 500 - 1, n / 500 + 1)}, "#7b1fa2")
        self.assertIn("1", p.bands)
        p.set_curves("1", n, n / 400, n / 500, color="#d62728")   # neue Kurven behalten das Band
        self.assertIn("1", p.bands)
        theme.manager().setze(theme.NACHT, speichern=False)
        theme.manager().setze(theme.TAG, speichern=False)
        p.set_legende("Ø 3 Läufe")
        self.assertEqual(p.legende_text(), "Ø 3 Läufe")
        p.remove("1")
        self.assertNotIn("1", p.bands)
        self.assertNotIn("1", p.items)

    def test_band_art_aus_json(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            row = type("R", (), {"ordner": d})()
            self.assertEqual(mp.band_art(row), "minmax")       # Datei fehlt
            with open(os.path.join(d, mittelwert.JSON_NAME), "w", encoding="utf-8") as fh:
                json.dump({"band": "sigma"}, fh)
            self.assertEqual(mp.band_art(row), "sigma")


# ============================================================================================== Hauptfenster
class Messen(unittest.TestCase):
    def setUp(self):
        from pyst import gui
        self.gui = gui
        self.w = gui.MainWindow()
        self.w.resize(1600, 1000)
        self.w.show()
        pump(0.1)
        db = self.w.db
        self.vid = db.save_vehicle({"hersteller": "Aprilia", "modell": "RS 125", "gemisch": "Vergaser"})
        self.sid = db.save_setup(self.vid, "Serie, HD 135", {"hauptduese": "135", "clip": "3", "zzp": "18"})
        self.w._reload_vehicle_combo(self.vid, self.sid)

    def tearDown(self):
        from conftest import close_window
        close_window(self.w, APP)
        theme.manager().setze(theme.TAG, speichern=False)

    def _laeufe(self, n=6):
        ids = []
        for f in sorted(x for x in os.listdir(DATA) if x.endswith(".xml"))[:n]:
            rid = self.w.db.import_file(os.path.join(DATA, f), vehicle_id=self.vid, setup_id=self.sid)
            self.w.db.update_run(rid, sichtbar=True)
            ids.append(rid)
        self.w._reload_runs()
        return ids

    def test_kopf_zustand_und_gas_weg(self):
        w = self.w
        from pyst.statusleiste import StatusFeld
        self.assertIsInstance(w.zustand, StatusFeld)
        self.assertIs(w.status, w.zustand)
        w._set_status("Verbunden – START drücken (F1)", "")
        self.assertEqual(w.zustand.stufe, theme.GUT)
        self.assertNotIn("(F1)", w.zustand.text())
        w._set_status("GAS WEG!", "#ff5555")
        self.assertEqual(w.zustand.stufe, "err")
        w.gas_weg.zeige("GAS WEG", "n Stop 10.800 1/min erreicht – Lauf wird ausgewertet")
        self.assertTrue(w.gas_weg.isVisible())
        w.gas_weg.verbergen()
        self.assertFalse(w.gas_weg.isVisible())

    def test_nachfrage_vor_start_und_schnappschuss(self):
        w = self.w
        z = w.setup_zeile
        self.assertTrue(w._setup_vor_start())               # unveraendert: keine Frage
        z.set_wert("hauptduese", "138")
        self.assertIn(mp.GEAENDERT.strip(), w.setup_combo.currentText())
        fragen = []
        w._setup_frage = lambda: fragen.append(1) or ""     # Abbrechen
        self.assertFalse(w._setup_vor_start())
        w._setup_frage = lambda: fragen.append(1) or "lauf"
        self.assertTrue(w._setup_vor_start())
        self.assertTrue(z.nur_lauf)
        self.assertTrue(w._setup_vor_start())               # fuer diesen Lauf bestaetigt: nicht nochmal fragen
        self.assertEqual(len(fragen), 2)
        werte, nur_lauf = w._setup_schnappschuss()
        self.assertTrue(nur_lauf)
        hd = [x for x in werte if x["feld"] == "hauptduese"][0]
        self.assertEqual((hd["wert"], hd["vorher"]), ("138", "135"))
        # Schalter aus: keine Frage
        z.set_nur_lauf(False)
        w.settings[mp.START_NACHFRAGE] = False
        w._setup_frage = lambda: self.fail("keine Nachfrage erwartet")
        self.assertTrue(w._setup_vor_start())

    def test_neues_setup_aus_aenderung(self):
        w = self.w
        w.setup_zeile.set_wert("hauptduese", "138")
        sid = w.setup_neu_anlegen(w.setup_zeile.neuer_name())
        self.assertIsNotNone(sid)
        self.assertEqual(w.db.setup(sid)["hauptduese"], "138")
        self.assertEqual(w.setup_combo.currentData(), sid)
        self.assertFalse(w.setup_zeile.ist_geaendert())

    def test_live_platz_je_fahrzeug(self):
        w = self.w
        w._live_gewaehlt(4, "egt2")
        self.assertEqual(w.db.live_plaetze(self.vid)[4]["kanal"], "egt2")
        self.assertEqual(w.live_kacheln[4].kanal, "egt2")
        vid2 = w.db.save_vehicle({"hersteller": "Honda", "modell": "NSR", "gemisch": "Vergaser"})
        w._reload_vehicle_combo(vid2)
        self.assertNotEqual(w.live_kacheln[4].kanal, "egt2")
        # ECU-Kanal ohne ECU: „ECU fehlt“, Titel aus ECU_TITEL
        k = w.live_kacheln[1]
        self.assertEqual(k.titel.text().split(" ▾")[0].strip(), "CLT")
        w._kacheln_zeigen(alle=True)
        self.assertEqual(k._fehlt_text, "ECU fehlt")

    def test_schloss_plaetze_und_referenz(self):
        w = self.w
        ids = self._laeufe(6)
        fest, roll = w._plaetze
        self.assertEqual(fest, [])
        self.assertEqual(len(roll), mp.MESSPLAETZE)
        self.assertNotIn(ids[0], roll)                      # aeltester faellt heraus
        self.assertTrue(w.schloss(ids[0]))                  # ... und kommt per Schloss zurueck
        self.assertEqual(w._plaetze[0], [ids[0]])
        self.assertEqual(len(w._plaetze[1]), mp.MESSPLAETZE - 1)
        self.assertIn(ids[0], w.diagramm_laeufe())
        self.assertTrue(w.schloss(ids[0]))
        self.assertEqual(w._plaetze[0], [])
        # Mittelwert aus 3 Laeufen als Referenz: gesperrt, dick, mit Band und Legende
        kurven = []
        for rid in ids[:3]:
            run, res, _ch, _p = w._entry(rid)
            kurven.append(mittelwert.kurven_aus_ergebnis(res, run))
        erg = mittelwert.mitteln(kurven, quellen=ids[:3])
        mw = mittelwert.speichern(w.db, erg, "Ø 3 Läufe", vehicle_id=self.vid, setup_id=self.sid, sichtbar=True)
        self.assertIsNone(w._entry(mw))                     # Mittelwerte nie ueber _entry
        self.assertTrue(w.referenz_laden(mw))
        self.assertEqual(w._plaetze[0], [mw])
        self.assertIn(mw, w.diagramm_laeufe())
        self.assertIn(str(mw), w.plot.bands)
        self.assertIn("Ø 3 Läufe", w.plot.legende_text())
        self.assertIn("min–max", w.plot.legende_text())
        # Bandart aus mittelwert.json (Auswertung): kein Band
        row = w.db.run(mw)
        pfad = os.path.join(row.ordner, mittelwert.JSON_NAME)
        with open(pfad, encoding="utf-8") as fh:
            meta = json.load(fh)
        meta["band"] = "kein"
        with open(pfad, "w", encoding="utf-8") as fh:
            json.dump(meta, fh)
        w._redraw_runs()
        self.assertNotIn(str(mw), w.plot.bands)
        self.assertNotIn("min–max", w.plot.legende_text())
        # Referenz-Dialog: angebotene zuerst, weitere erst auf Wunsch
        dlg = mp.ReferenzDialog([w.db.run(mw)], [], w)
        self.assertEqual(dlg.gewaehlt(), mw)
        self.assertFalse(dlg.b_weitere.isVisibleTo(dlg))
        dlg2 = mp.ReferenzDialog([], [w.db.run(mw)], w)
        self.assertTrue(dlg2.b_weitere.isChecked())
        self.assertEqual(dlg2.liste.count(), 1)

    def test_laufliste_als_seitenleiste_und_cursorzeile(self):
        w = self.w
        self._laeufe(2)
        L = w.laeufe_leiste
        self.assertIn("Plätze", L.streifen.text())
        L.set_angepinnt(True)
        w._leiste_gemerkt()
        self.assertTrue(w.settings["laeufe_leiste"]["angepinnt"])
        L.set_angepinnt(False)
        self.assertFalse(w.cursor_table.isVisible())
        w.cursor_toggle.setChecked(True)
        pump(0.05)
        self.assertTrue(w.cursor_table.isVisible())
        w.cursor_toggle.setChecked(False)


if __name__ == "__main__":
    unittest.main()
