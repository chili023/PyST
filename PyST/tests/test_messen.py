"""Stufe 2, Reiter Messen: Zustand neben START, Setup-Werte-Zeile mit Nachfrage vor START und Schnappschuss,
Live-Plaetze je Fahrzeug, Schloss/Plaetze der Laufliste, Mittelwert als Referenz mit Band, Band im DynoPlot."""
import gc
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
    def tearDown(self):
        # Widgets ohne Eltern (Zeilen, Diagramme) mit Lambda-Zyklen im Hauptthread aufraeumen, nicht spaeter durch
        # eine Garbage Collection in einem Hintergrund-Thread (Verbinden, Klima)
        gc.collect()
        APP.processEvents()

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

    def test_setup_zeile_variable_plaetze(self):
        """Plaetze hinzufuegen (＋), Feld waehlen, entfernen; hoechstens SCHNELLWERT_MAX; jede Aenderung meldet die
        ganze Liste."""
        from pyst.db import SCHNELLWERT_MAX
        z = mp.SetupZeile()
        gemeldet = []
        z.plaetze_geaendert.connect(gemeldet.append)
        z.set_setup(5, "Serie", {"hauptduese": "135", "clip": "3", "zzp": "18", "auspuff": "Arrow"},
                    ["hauptduese", "clip", "zzp"], "Aprilia RS 125", "Vergaser")
        self.assertEqual(len(z.edits), 3)
        self.assertTrue(z.platz_hinzufuegen("auspuff"))
        self.assertEqual(z.felder, ["hauptduese", "clip", "zzp", "auspuff"])
        self.assertEqual(len(z.edits), 4)
        self.assertEqual(z.edits[3].text(), "Arrow")
        self.assertEqual(gemeldet[-1], ["hauptduese", "clip", "zzp", "auspuff"])
        # Menue des Knopfs ＋: Feld, das schon auf einem Platz steht, ist gesperrt
        m = z.b_plus.menu()
        z._menue(-1, m)
        hd = next(a for a in m.actions() if a.text().startswith("Hauptdüse"))
        self.assertFalse(hd.isEnabled())
        # Titel-Menue eines Platzes: Feld wechseln, Platz entfernen
        m = z.titel_knoepfe[1].menu()
        z._menue(1, m)
        weg = next(a for a in m.actions() if a.text() == "Platz entfernen")
        weg.trigger()
        self.assertEqual(z.felder, ["hauptduese", "zzp", "auspuff"])
        self.assertEqual(len(z.edits), 3)
        self.assertEqual(z.edits[1].text(), "18")
        z.set_wert("auspuff", "Akrapovic")                 # Aenderung bleibt auch nach Entfernen des Platzes
        z.platz_entfernen(2)
        self.assertEqual(z.aenderungen(), {"auspuff": "Akrapovic"})
        while z.platz_hinzufuegen(""):
            pass
        self.assertEqual(len(z.felder), SCHNELLWERT_MAX)
        self.assertFalse(z.b_plus.isVisibleTo(z))
        z.set_felder([])
        self.assertEqual((z.felder, len(z.edits)), ([], 0))
        self.assertTrue(z.b_plus.isVisibleTo(z))

    def test_hinweis_kuerzt_statt_umzubrechen(self):
        """Ruhetext der Setup-Zeile (KurzText) bricht die Zeile nie um, er kuerzt sich bzw. bleibt leer."""
        from pyst.umbruch import UmbruchLayout
        w = QtWidgets.QWidget()
        lay = UmbruchLayout(w, abstand=6)
        lay.setContentsMargins(0, 0, 0, 0)
        b = QtWidgets.QPushButton("Platz")
        b.setFixedWidth(200)
        lay.add(b)
        k = mp.KurzText("Setup unverändert: Season 2026")
        lay.add(k, 1)
        self.assertEqual(lay.heightForWidth(203), lay.heightForWidth(900))
        w.resize(240, 40)
        w.show()
        pump(0.05)
        self.assertEqual(k.text(), "Setup unverändert: Season 2026")
        self.assertEqual(QtWidgets.QLabel.text(k), "")                 # 34 px: zu schmal -> leer
        w.resize(900, 40)
        pump(0.05)
        self.assertEqual(QtWidgets.QLabel.text(k), "Setup unverändert: Season 2026")
        w.close()

    def test_achsfelder_ohne_exponent_und_egt_ohne_daten(self):
        from pyst.plots import feld_zahl
        self.assertEqual([feld_zahl(v) for v in (12500, 12500.0, 0.85, 1.3, 0.005, 18.0, 650.5)],
                         ["12500", "12500", "0.85", "1.3", "0.005", "18", "650.5"])
        p = DynoPlot()
        p.set_limits({"n_hi": 12500, "n_lo": 3000, "lam_hi": 1.25})
        self.assertEqual((p.fields["n_hi"].text(), p.fields["n_lo"].text(), p.fields["lam_hi"].text()),
                         ("12500", "3000", "1.25"))
        # unten rechts EGT (Board) + ein ECU-Kanal ohne Werte, EGT ohne Sensor (0): ganzzahlige Skala bis ~1000 °C
        p.set_lower(["lam", "x:lambdaValue"], ["egt1", "x:egt"], {"x:lambdaValue": ("Lambda (ECU)", "λ"),
                                                                  "x:egt": ("EGT (ECU)", "°C")})
        n = np.linspace(3000, 12500, 60)
        p.set_curves("1", n, n / 400, n / 500, lam=np.full(60, 0.92), egt1=np.zeros(60))
        p.auto_scale()
        lo, hi, st = p.bottom._scales[1]
        self.assertEqual(lo, 0.0)
        self.assertGreaterEqual(hi, 900.0)
        self.assertLessEqual(hi, 1000.0)
        self.assertEqual(st, round(st))
        ticks = [t for t, _l in p.bottom.pi.getAxis("right")._tickLevels[0]]
        self.assertTrue(all(abs(t - round(t)) < 1e-9 for t in ticks))
        self.assertNotIn("e+", p.fields["egt_hi"].placeholderText())
        self.assertEqual(p.fields["egt_hi"].placeholderText(), str(int(hi)))
        # Achstitel links: nur der Kanal mit Werten -> vollstaendig „Lambda λ“
        self.assertEqual(p.bottom._label_shown[0] if p.bottom._label_shown else p.bottom._labels[0][0], "Lambda λ")
        # Werte, die alle gleich sind (z.B. konstanter ECU-Kanal), geben einen echten Bereich
        p.set_lower(["lam"], ["x:egt"], {"x:egt": ("EGT (ECU)", "°C")})
        p.set_curves("1", n, n / 400, n / 500, lam=np.full(60, 0.92), extra={"egt": np.full(60, 0.0)})
        p.auto_scale()
        lo, hi, st = p.bottom._scales[1]
        self.assertGreater(hi - lo, 0.5)
        self.assertGreater(st, 0.1)

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
        gc.collect()
        APP.processEvents()

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

    def test_schnellwert_plaetze_je_fahrzeug(self):
        """5 Plaetze fuer ein Fahrzeug: gespeichert, beim Fahrzeugwechsel eigene Plaetze, Auswertung zeigt die Spalten."""
        w = self.w
        z = w.setup_zeile
        z.platz_hinzufuegen("auspuff")
        z.platz_hinzufuegen("kerze")
        self.assertEqual(w.db.schnellwert_plaetze(self.vid), ["hauptduese", "clip", "zzp", "auspuff", "kerze"])
        vid2 = w.db.save_vehicle({"hersteller": "Honda", "modell": "NSR", "gemisch": "Vergaser"})
        w._reload_vehicle_combo(vid2)
        self.assertEqual(len(z.edits), 3)
        w._reload_vehicle_combo(self.vid, self.sid)
        self.assertEqual(z.felder, ["hauptduese", "clip", "zzp", "auspuff", "kerze"])
        self.assertEqual(len(z.edits), 5)
        ids = self._laeufe(2)
        w.viewer.add_runs(ids)
        pump(0.1)
        self.assertEqual(w.viewer.leiste.felder, ["hauptduese", "clip", "zzp", "auspuff", "kerze"])

    def test_start_wird_abbrechen(self):
        """Nur noch Trennen | Lauf-Einstellungen | START; im Lauf ist START der rote Abbrechen-Knopf (Esc)."""
        w, gui = self.w, self.gui
        self.assertFalse(hasattr(w, "abort_btn"))
        self.assertFalse(hasattr(w, "b_ratio"))
        self.assertEqual(w.start_btn.text(), mp.START_TEXT)
        self.assertFalse(w.start_btn.isEnabled())           # nicht verbunden
        self.assertEqual(w.connect_main.text(), "Verbinden")
        w.page_settings.port_combo.setEditText(gui.DEFAULT_SIM_PORT)
        self.assertTrue(w.on_connect())
        try:
            self.assertEqual(w.connect_main.text(), "Trennen")
            self.assertTrue(w.start_btn.isEnabled())
            w.start_btn.click()                              # START
            self.assertIn(w.ctrl.state, gui.ACTIVE)
            self.assertEqual(w.start_btn.text(), mp.ABBRECHEN_TEXT)
            self.assertTrue(w.start_btn.isEnabled())
            self.assertIn(theme.tokens()["err_bg"], w.start_btn.styleSheet())
            self.assertFalse(w.lauf_btn.isEnabled())
            self.assertFalse(w.connect_main.isEnabled())
            w.on_start()                                     # F1 im Lauf: nichts
            self.assertIn(w.ctrl.state, gui.ACTIVE)
            w.start_btn.click()                              # Abbrechen
            self.assertEqual(w.ctrl.state, gui.ABORTED)
            self.assertEqual(w.start_btn.text(), mp.START_TEXT)
            self.assertNotIn(theme.tokens()["err_bg"], w.start_btn.styleSheet())
            self.assertTrue(w.lauf_btn.isEnabled())
            w.on_start()                                     # F1
            self.assertIn(w.ctrl.state, gui.ACTIVE)
            w.on_abort()                                     # Esc
            self.assertEqual(w.ctrl.state, gui.ABORTED)
        finally:
            w.on_connect()                                   # trennen
        self.assertEqual(w.connect_main.text(), "Verbinden")

    def test_lauf_einstellungen_popup(self):
        """Popup am Knopf: Übersetzungsart, Wert, Filter und Grenzen wirken auf dieselben Felder wie Einstellungen ›
        Rolle/Lauf (in beide Richtungen); der Knopf zeigt den Stand."""
        from pyst import gui
        w = self.w
        s = w.page_settings
        s.fields["ratio"].setValue(6.85)
        self.assertIn("6,850 (von Hand)", w.lauf_btn.toolTip())
        self.assertIn("6,850", w.lauf_btn.stand())
        w._lauf_popup_zeigen()
        p = w.lauf_popup
        self.assertTrue(p.isVisible())
        # Art: Funkenmessung -> Einstellung und Rolle/Lauf-Seite
        p.art["funken"].setChecked(True)
        self.assertEqual(s.ratio_art.currentData(), "funken")
        self.assertEqual(w.settings[gui.UEBERSETZUNG_ART], "funken")
        self.assertIn("Funken", w.lauf_btn.toolTip())
        s.ratio_art.setCurrentIndex(s.ratio_art.findData("hand"))       # andersherum
        self.assertTrue(p.art["hand"].isChecked())
        # Werte in beide Richtungen
        p.spins["ratio"].setValue(7.1)
        self.assertAlmostEqual(s.fields["ratio"].value(), 7.1)
        self.assertIn("7,100", w.lauf_btn.toolTip())
        s.fields["n_stop"].setValue(10800)
        self.assertEqual(p.spins["n_stop"].value(), 10800)
        p.spins["n_min"].setValue(4500)
        self.assertEqual(s.params().n_min, 4500)
        # Filter: Standard, geaendert, Messpunkte
        p._filter_standard()
        self.assertIn("Standard", w.lauf_btn.toolTip())
        p.spins["ma_s"].setValue(2.0)
        self.assertAlmostEqual(s.fields["ma_s"].value(), 2.0)
        self.assertIn("2,00 / 0,75 s", w.lauf_btn.toolTip())
        p.filter_mode.setCurrentIndex(1)
        self.assertFalse(s.filter_seconds)
        self.assertIn("Pkt.", w.lauf_btn.toolTip())
        s.set_filter_seconds(True)                            # ohne Signal (wie set_params): abgleichen beim Oeffnen
        p.hide()
        w._lauf_popup_zeigen()
        self.assertEqual(p.filter_mode.currentIndex(), 0)
        # Schliessen speichert; Link fuehrt zur ganzen Seite
        p.b_alle.click()
        self.assertFalse(p.isVisible())
        self.assertIs(w.current_page(), s)
        self.assertAlmostEqual(w.settings["params"]["ratio"], 7.1)
        # Unterzeile passt in den Knopf
        fm = w.lauf_btn._fm(11)
        self.assertLessEqual(fm.horizontalAdvance(w.lauf_btn.stand()), w.lauf_btn.width())

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
