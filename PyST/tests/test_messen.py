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

from PySide6 import QtCore, QtWidgets  # noqa: E402

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

    def test_fahnen_verteilen(self):
        from pyst.plots import fahnen_verteilen
        self.assertEqual(fahnen_verteilen([], 10, 0, 100), [])
        self.assertEqual(fahnen_verteilen([50.0], 10, 0, 100), [50.0])
        ys = fahnen_verteilen([40.0, 42.0, 41.0], 10, 0, 100)
        s = sorted(ys)
        self.assertTrue(all(b - a >= 12 - 1e-9 for a, b in zip(s, s[1:])))
        self.assertEqual(ys[0], min(ys))                         # Reihenfolge bleibt (oberer bleibt oben)
        unten = fahnen_verteilen([95.0, 96.0], 10, 0, 100)        # am unteren Rand: nach oben geschoben
        self.assertLessEqual(max(unten) + 10, 100)
        self.assertGreaterEqual(min(unten), 0)

    def test_faehnchen_im_diagramm(self):
        """Fähnchen je Kurve in der Laufarbe, gleiche Farbe zusammengefasst, Werte deutsch, weg beim Verlassen."""
        p = DynoPlot()
        p.resize(900, 600)
        p.show()
        n = np.linspace(3000, 11000, 81)
        p.set_curves("1", n, n / 400, n / 500, lam=np.full(81, 0.9), egt1=np.full(81, 820.0), color="#1f77b4")
        p.set_curves("2", n, n / 400 + 1, n / 500 + 1, color="#1f77b4")     # gleiche Farbe
        p.set_curves("3", n, n / 400 + 3, n / 500 + 2, color="#d62728")
        pump(0.05)
        verlassen = []
        p.cursor_left.connect(lambda: verlassen.append(1))
        p.zeige_cursor(8000.0)
        t = p.fahnen.texte()
        self.assertIn("20,0 / 21,0 PS", t)                       # Laeufe 1 und 2 (gleiche Farbe) zusammen
        self.assertIn("23,0 PS", t)
        self.assertIn("16,0 / 17,0 Nm", t)
        self.assertIn("λ 0,90", t)
        self.assertIn("820 °C", t)
        voll = {f["text"]: f["voll"] for f in p.fahnen.fahnen}
        self.assertTrue(voll["23,0 PS"])                          # Leistung gefuellt, Drehmoment umrandet
        self.assertFalse(voll["18,0 Nm"])
        farben = {f["text"]: f["farbe"] for f in p.fahnen.fahnen}
        self.assertEqual(farben["23,0 PS"], theme.laufarbe("#d62728"))
        p.set_lambda_mode(False)
        p.zeige_cursor(8000.0)
        self.assertIn("AFR 13,2", p.fahnen.texte())
        p.zeige_cursor(2000.0)                                    # ausserhalb der Laeufe: keine Fähnchen
        self.assertEqual(p.fahnen.texte(), [])
        p.zeige_cursor(8000.0)
        p._leave()
        self.assertEqual(p.fahnen.texte(), [])
        self.assertFalse(p.fahnen.isVisible())
        self.assertEqual(verlassen, [1])
        p.close()

    def test_auto_mittel_quellen(self):
        """Auswahl fuer „Ø automatisch mitteln“: gleiches Setup wie der neueste Lauf (Fahrzeug, Setup-ID und Werte),
        mind. 2, Mittelwerte und Dauermessungen nicht, neueste zuerst."""
        from types import SimpleNamespace as R
        from pyst.db import ART_DAUERMESSUNG, ART_LAUF, ART_MITTELWERT

        def lauf(i, datum, sid=5, vid=1, art=ART_LAUF):
            return R(id=i, datum=datum, setup_id=sid, vehicle_id=vid, art=art, ist_mittelwert=art == ART_MITTELWERT)
        s = {"hauptduese": "135", "clip": "3"}
        rows = [lauf(1, "2026-11-09T10:00"), lauf(2, "2026-11-09T10:05"), lauf(3, "2026-11-09T10:10"),
                lauf(4, "2026-11-09T10:20", art=ART_MITTELWERT), lauf(5, "2026-11-09T09:00", sid=6),
                lauf(6, "2026-11-09T09:30", art=ART_DAUERMESSUNG), lauf(7, "2026-11-09T09:40", vid=2)]
        setups = {i: dict(s) for i in range(1, 8)}
        self.assertEqual(mp.auto_mittel_quellen(rows, setups), [3, 2, 1])
        setups[2] = {"hauptduese": "138", "clip": "3"}                  # nur fuer diesen Lauf geaendert
        self.assertEqual(mp.auto_mittel_quellen(rows, setups), [3, 1])
        setups[1] = {"hauptduese": "138", "clip": "3"}
        self.assertEqual(mp.auto_mittel_quellen(rows, setups), [])      # nur einer mit dem Setup des neuesten
        self.assertEqual(mp.auto_mittel_quellen([lauf(5, "x", sid=6)], setups), [])
        self.assertEqual(mp.auto_mittel_quellen([], {}), [])
        # neuester hat ein anderes Setup: der Mittelwert gilt diesem
        rows.append(lauf(8, "2026-11-09T11:00", sid=6))
        setups[8] = dict(s)
        self.assertEqual(mp.auto_mittel_quellen(rows, setups), [8, 5])
        # ohne Setup: alle ohne Setup desselben Fahrzeugs
        nackt = [lauf(1, "a", sid=None), lauf(2, "b", sid=None), lauf(3, "c")]
        self.assertEqual(mp.auto_mittel_quellen(nackt, {1: {}, 2: {}, 3: s}), [])
        nackt.append(lauf(4, "d", sid=None))
        self.assertEqual(mp.auto_mittel_quellen(nackt, {1: {}, 2: {}, 3: s, 4: {}}), [4, 2, 1])

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
        for key, (std, _t, _tip) in mp.ANZEIGE_OPTIONEN.items():     # Einstellungen gelten fuer alle Tests
            self.w.anzeige_setzen(key, std)
        self.w.auto_mittel_setzen(False)
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
        """Nachfrage nur bei unveraendertem Setup; Abbrechen startet nicht, „Unverändert starten“ laesst alles."""
        w = self.w
        z = w.setup_zeile
        fragen = []
        w._start_frage = lambda felder: fragen.append(list(felder)) and None     # Abbrechen
        self.assertFalse(w._setup_vor_start())
        self.assertEqual(fragen[0], [("hauptduese", "135"), ("clip", "3"), ("zzp", "18")])
        w._start_frage = lambda felder: fragen.append(list(felder)) or {}         # unveraendert starten
        self.assertTrue(w._setup_vor_start())
        self.assertFalse(z.ist_geaendert())
        self.assertEqual(len(fragen), 2)
        # schon geaendert: keine Frage, die Werte gelten fuer diesen Lauf
        z.set_wert("hauptduese", "138")
        self.assertIn(mp.GEAENDERT.strip(), w.setup_combo.currentText())
        w._start_frage = lambda felder: self.fail("keine Nachfrage bei geändertem Setup")
        self.assertTrue(w._setup_vor_start())
        self.assertTrue(z.nur_lauf)
        werte, nur_lauf = w._setup_schnappschuss()
        self.assertTrue(nur_lauf)
        hd = [x for x in werte if x["feld"] == "hauptduese"][0]
        self.assertEqual((hd["wert"], hd["vorher"]), ("138", "135"))
        # Schalter aus, keine belegten Plaetze, kein Fahrzeug: keine Frage
        z.verwerfen()
        w.settings[mp.START_NACHFRAGE] = False
        self.assertTrue(w._setup_vor_start())
        w.settings[mp.START_NACHFRAGE] = True
        z.set_felder(["", ""])
        self.assertTrue(w._setup_vor_start())
        z.set_felder(["hauptduese"])
        w._reload_vehicle_combo(None)
        self.assertTrue(w._setup_vor_start())

    def test_nachfrage_uebernehmen(self):
        """„Übernehmen und starten“ traegt die Werte in SETUP-WERTE ein wie getippt (gelb, vorher …, dieser Lauf)."""
        w = self.w
        z = w.setup_zeile
        w._start_frage = lambda felder: {"hauptduese": "140", "zzp": "20"}
        self.assertTrue(w._setup_vor_start())
        self.assertEqual(z.aenderungen(), {"hauptduese": "140", "zzp": "20"})
        self.assertTrue(z.nur_lauf)
        self.assertEqual(z.edits[0].text(), "140")
        self.assertTrue(z.vorher[0].isVisibleTo(z))
        self.assertEqual(z.vorher[0].text(), "vorher 135")
        self.assertIn(theme.tokens()["changed_bg"], z.edits[0].styleSheet())
        self.assertIn("HD 135 → HD 140", z.marke_text())
        self.assertIn("nur für diesen Lauf", z.marke_text())
        # naechster START: geaendert, also keine Frage mehr
        w._start_frage = lambda felder: self.fail("keine Nachfrage erwartet")
        self.assertTrue(w._setup_vor_start())

    def test_nachfrage_fenster(self):
        """Fenster StartNachfrage: Felder vorbelegt, Unverändert starten ist Standard mit Fokus, Übernehmen erst nach
        einer Aenderung (dann Standard), Abbrechen = Rejected."""
        w = self.w
        dlg = mp.StartNachfrage([("hauptduese", "135"), ("clip", "3")], "Serie, HD 135", w)
        self.assertEqual(dlg.windowTitle(), "Setup unverändert")
        self.assertIn("Setup unverändert – Änderungen eintragen?", dlg.frage.text())
        self.assertEqual(dlg.edits["hauptduese"].text(), "135")
        self.assertTrue(dlg.b_start.isDefault())
        self.assertFalse(dlg.b_ueber.isEnabled())
        dlg.show()
        pump(0.05)
        self.assertIs(APP.focusWidget(), dlg.b_start)
        dlg.edits["clip"].setText("4")
        dlg.edits["clip"].textEdited.emit("4")
        self.assertTrue(dlg.b_ueber.isEnabled())
        self.assertTrue(dlg.b_ueber.isDefault())
        self.assertFalse(dlg.b_start.isDefault())
        dlg.b_ueber.click()
        self.assertEqual(dlg.result(), QtWidgets.QDialog.Accepted)
        self.assertEqual(dlg.aenderungen(), {"clip": "4"})
        # Unverändert starten: keine Aenderungen, auch wenn etwas getippt wurde
        dlg2 = mp.StartNachfrage([("hauptduese", "135")], "", w)
        dlg2.edits["hauptduese"].setText("136")
        dlg2.b_start.click()
        self.assertEqual(dlg2.result(), QtWidgets.QDialog.Accepted)
        self.assertEqual(dlg2.aenderungen(), {})
        dlg3 = mp.StartNachfrage([("hauptduese", "135")], "", w)
        dlg3.b_ab.click()
        self.assertEqual(dlg3.result(), QtWidgets.QDialog.Rejected)
        for d in (dlg, dlg2, dlg3):
            d.deleteLater()

    def test_nachfrage_start_knopf_und_zange(self):
        """START-Knopf fragt (unveraendertes Setup), die Zangentaste „Neuer Lauf“ nicht."""
        w, gui = self.w, self.gui
        fragen = []
        w._start_frage = lambda felder: fragen.append(felder) and None          # Abbrechen
        w.page_settings.port_combo.setEditText(gui.DEFAULT_SIM_PORT)
        self.assertTrue(w.on_connect())
        try:
            w.start_btn.click()
            self.assertEqual(len(fragen), 1)
            self.assertNotIn(w.ctrl.state, gui.ACTIVE)                          # abgebrochen: kein Lauf
            w._taste_lauf()                                                      # Zangentaste „Neuer Lauf“
            self.assertEqual(len(fragen), 1)
            self.assertIn(w.ctrl.state, gui.ACTIVE)
            w.on_abort()
        finally:
            w.on_connect()

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
        self.assertEqual(w.laeufe_hinzufuegen([mw]), 1)
        self.assertEqual(w.laeufe_hinzufuegen([mw]), 0)    # liegt schon da: nicht doppelt
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

    def _mittelwert(self, ids, name="Ø 3 Läufe"):
        kurven = []
        for rid in ids:
            run, res, _ch, _p = self.w._entry(rid)
            kurven.append(mittelwert.kurven_aus_ergebnis(res, run))
        erg = mittelwert.mitteln(kurven, quellen=list(ids))
        return mittelwert.speichern(self.w.db, erg, name, vehicle_id=self.vid, setup_id=self.sid, sichtbar=False)

    def test_laeufe_hinzufuegen_knopf(self):
        """Seitenleiste Läufe: „＋“ statt „Referenz laden …“ oben im Kopf, kein ReferenzDialog mehr."""
        w = self.w
        self.assertFalse(hasattr(w, "b_ref"))
        self.assertFalse(hasattr(mp, "ReferenzDialog"))
        self.assertEqual(w.b_hinzu.text(), "＋")
        self.assertIn("Läufe hinzufügen …", w.b_hinzu.toolTip())
        self.assertIs(w.b_hinzu.parentWidget(), w.laeufe_leiste.feld)
        self.assertEqual(w.laeufe_leiste.kopf.indexOf(w.b_hinzu), 1)       # gleich neben dem Titel
        texte = [b.text() for b in w.laufliste.findChildren(QtWidgets.QPushButton)]
        self.assertNotIn("Referenz laden …", texte)

    def test_laeufe_dialog(self):
        """Läufe hinzufügen: Läufe und Mittelwerte (Ø), Filter Fahrzeug/Art/Suche, gesperrte nicht wählbar,
        Plätze-Grenze, Doppelklick."""
        w = self.w
        ids = self._laeufe(6)
        mw = self._mittelwert(ids[:3])
        w.db.update_run(ids[2], notiz="Luftfilter offen")
        vid2 = w.db.save_vehicle({"hersteller": "Honda", "modell": "NSR", "gemisch": "Vergaser"})
        fremd = w.db.import_file(os.path.join(DATA, sorted(x for x in os.listdir(DATA) if x.endswith(".xml"))[0]),
                                 vehicle_id=vid2)
        w.schloss(ids[0])
        dlg = mp.LaeufeDialog(w.db, self.vid, w.db.gesperrte_plaetze(self.vid), w._plaetze[1], w)
        self.assertEqual(dlg.windowTitle(), "Läufe hinzufügen")
        self.assertEqual([dlg.liste.headerItem().text(c) for c in range(5)],
                         ["Datum/Zeit", "Setup", "Notiz", "Pmax", "Mmax"])
        self.assertEqual(dlg.frei, mp.MESSPLAETZE - 1)

        def zeilen():
            return {dlg.liste.topLevelItem(i).data(0, mp.QtCore.Qt.UserRole): dlg.liste.topLevelItem(i)
                    for i in range(dlg.liste.topLevelItemCount())}
        z = zeilen()
        self.assertEqual(set(z), set(ids) | {mw})               # aktuelles Fahrzeug: ohne den fremden Lauf
        self.assertIn("Ø 3 Läufe", z[mw].text(2))               # Mittelwert deutlich: Name mit Ø, fett
        self.assertTrue(z[mw].font(0).bold())
        self.assertFalse(z[ids[1]].font(0).bold())
        self.assertEqual(z[ids[2]].text(2), "Luftfilter offen")
        self.assertEqual(z[ids[1]].text(1), "Serie, HD 135")
        self.assertIn("PS", z[ids[1]].text(3))
        self.assertFalse(z[ids[0]].flags() & mp.QtCore.Qt.ItemIsSelectable)   # schon gesperrt
        dlg.fz.setCurrentIndex(0)                                 # alle Fahrzeuge
        self.assertIn(fremd, zeilen())
        self.assertIn("Honda", zeilen()[fremd].text(1))
        dlg.art.setCurrentIndex(2)                                # nur Mittelwerte
        self.assertEqual(set(zeilen()), {mw})
        dlg.art.setCurrentIndex(1)                                # nur Messungen
        self.assertNotIn(mw, zeilen())
        dlg.art.setCurrentIndex(0)
        dlg.suche.setText("luftfilter")
        self.assertEqual(set(zeilen()), {ids[2]})
        dlg.suche.setText("")
        # Mehrfachauswahl, Grenze der freien Plaetze
        z = zeilen()
        for rid in ids[1:5]:
            z[rid].setSelected(True)
        self.assertEqual(set(dlg.gewaehlt()), set(ids[1:5]))
        self.assertTrue(dlg.b_ok.isEnabled())
        self.assertEqual(dlg.b_ok.text(), "Hinzufügen (4)")
        z[mw].setSelected(True)
        self.assertFalse(dlg.b_ok.isEnabled())                    # 5 gewaehlt, 4 frei
        self.assertIn("nur 4 von 5 Plätzen frei", dlg.status.text())
        # Doppelklick nimmt einen Lauf direkt
        dlg.liste.itemDoubleClicked.emit(z[mw], 0)
        self.assertEqual(dlg.result(), QtWidgets.QDialog.Accepted)
        self.assertEqual(dlg.gewaehlt(), [mw])
        dlg.deleteLater()

    def test_laeufe_hinzufuegen_plaetze(self):
        """Gewählte Läufe kommen gesperrt auf die Plätze, Mittelwert mit Band; doppelte nicht; zu wenig Plätze:
        Meldung, nichts ändert sich."""
        w = self.w
        ids = self._laeufe(6)
        mw = self._mittelwert(ids[:3])
        self.assertEqual(w.laeufe_hinzufuegen([ids[0], mw, ids[0]]), 2)
        self.assertEqual(w._plaetze[0], [ids[0], mw])
        self.assertTrue(w.db.run(mw).sichtbar)
        self.assertIn(str(mw), w.plot.bands)                     # Mittelwert wie die Referenz: Band min–max
        self.assertIn(ids[0], w.diagramm_laeufe())
        roll = w._plaetze[1]
        self.assertEqual(w.laeufe_hinzufuegen([roll[0]]), 1)       # rollierender Lauf wird gesperrt, nicht doppelt
        self.assertEqual(w._plaetze[0], [ids[0], mw, roll[0]])
        self.assertNotIn(roll[0], w._plaetze[1])
        meldungen = []
        alt = QtWidgets.QMessageBox.information
        QtWidgets.QMessageBox.information = lambda *a, **k: meldungen.append(a[2])
        try:
            offen = [i for i in ids if i not in w._plaetze[0]]
            self.assertEqual(w.laeufe_hinzufuegen(offen[:3]), -1)  # 3 neu, nur 2 frei
            self.assertEqual(len(meldungen), 1)
            self.assertIn("nur 2 von 5 Plätzen frei", meldungen[0])
            self.assertEqual(len(w._plaetze[0]), 3)
            self.assertEqual(w.laeufe_hinzufuegen(offen[:2]), 2)
            self.assertEqual(len(w._plaetze[0]), mp.MESSPLAETZE)
            w._laeufe_dialog()                                   # alle gesperrt: Meldung statt Fenster
            self.assertEqual(len(meldungen), 2)
            self.assertIn("Schloss", meldungen[1])
        finally:
            QtWidgets.QMessageBox.information = alt
        self.assertTrue(w.schloss(ids[0]))                       # Schloss loesen geht wie bisher
        self.assertEqual(len(w._plaetze[0]), mp.MESSPLAETZE - 1)

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

    def test_laufliste_als_seitenleiste(self):
        w = self.w
        self._laeufe(2)
        L = w.laeufe_leiste
        self.assertIn("Plätze", L.streifen.text())
        L.set_angepinnt(True)
        w._leiste_gemerkt()
        self.assertTrue(w.settings["laeufe_leiste"]["angepinnt"])
        L.set_angepinnt(False)
        self.assertFalse(hasattr(w, "cursor_table"))           # Zeile „Werte am Cursor“ gibt es nicht mehr

    def test_cursor_faehnchen_und_rechte_spalte(self):
        """Maus im Diagramm: Fähnchen je Kurve, die Live-Kacheln zeigen den neuesten Lauf an der Cursor-Drehzahl;
        Maus raus: wieder live."""
        w = self.w
        ids = self._laeufe(3)
        pump(0.2)
        neu = max((w.db.run(i) for i in ids), key=lambda r: r.datum)
        self.assertEqual(w._neuester_lauf(), str(neu.id))
        n = w.plot.data[str(neu.id)]["n"]
        x = float(np.nanmin(n) + 0.6 * (np.nanmax(n) - np.nanmin(n)))
        w.plot.zeige_cursor(x)
        pump(0.05)
        texte = w.plot.fahnen.texte()
        self.assertTrue(any(t.endswith(" PS") for t in texte), texte)
        self.assertTrue(any(t.endswith(" Nm") for t in texte), texte)
        self.assertTrue(w.plot.fahnen.isVisible())
        # Fähnchen eines Diagramms ueberlappen sich nicht
        for bereich in ({"x": f["x"], "y": f["y"], "w": f["w"], "h": f["h"]} for f in w.plot.fahnen.fahnen):
            andere = [f for f in w.plot.fahnen.fahnen if (f["x"], f["y"]) != (bereich["x"], bereich["y"])]
            for f in andere:
                ueber = not (f["x"] >= bereich["x"] + bereich["w"] or bereich["x"] >= f["x"] + f["w"]
                             or f["y"] >= bereich["y"] + bereich["h"] or bereich["y"] >= f["y"] + f["h"])
                self.assertFalse(ueber, (bereich, f))
        # rechte Spalte: Werte des neuesten Laufs, Titel mit Zeit und Drehzahl
        self.assertEqual(w._cursor_lauf, str(neu.id))
        titel = w.live_titel.text()
        self.assertIn("LAUF", titel)
        self.assertIn("1/min", titel)
        werte = w.lauf_werte_bei(str(neu.id), x)
        self.assertAlmostEqual(w.k_drehzahl.wert, werte["n"], places=3)
        lam = next(k for k in w.live_kacheln if k.kanal == "lam")
        self.assertEqual(lam.quelle.text(), "Lauf")
        self.assertIsNotNone(werte.get("v"))                    # Kanaele des gespeicherten Laufs (Rolle km/h)
        # Live-Takt ueberschreibt die Laufwerte nicht
        w._kacheln_takt()
        self.assertEqual(w._cursor_lauf, str(neu.id))
        # Maus raus: wieder live
        w.plot._leave()
        self.assertIsNone(w._cursor_lauf)
        self.assertIn("LIVE", w.live_titel.text())
        self.assertEqual(w.plot.fahnen.texte(), [])
        self.assertNotEqual(lam.quelle.text(), "Lauf")

    def test_anweisung_gross(self):
        """GO gross bis die Messung beginnt, waehrend der Messung nichts gross (nur das Feld neben START), am
        Laufende GAS WEG! bzw. GAS WEG – KUPPLUNG ZIEHEN! bis das Ausrollen fertig ist; feste Schrift; Option aus:
        nur GAS WEG."""
        from types import SimpleNamespace
        from pyst.runner import COAST, DONE, RUN, WAIT
        w = self.w
        gas = {"weg": False}
        ctrl = SimpleNamespace(state=WAIT, gas_off=lambda: gas["weg"], ausrollen=False,
                               params=SimpleNamespace(n_stop=10800))
        lv = SimpleNamespace(n_calc=9500.0)
        alt = w.ctrl
        w.ctrl = ctrl
        a = w.anweisung
        try:
            self.assertTrue(w.anzeige_an(mp.ANWEISUNG_GROSS))       # Standard an
            w._laufzustand_zeigen(WAIT, lv)
            self.assertTrue(a.isVisible())
            self.assertEqual(a.text(), "GO – Vollgas!")
            self.assertEqual(a.stufe, theme.GUT)
            px_go = a.schrift_px()
            ctrl.state = RUN                                        # n vom Gas: Messung beginnt -> GO sofort weg
            w._laufzustand_zeigen(RUN, lv)
            self.assertFalse(a.isVisible())
            self.assertEqual(w.zustand.text(), "Messung · 9.500 1/min")   # nur klein neben START
            lv.n_calc = 10500.0
            w._laufzustand_zeigen(RUN, lv)
            self.assertFalse(a.isVisible())                         # waehrend der Messung nichts gross
            self.assertEqual(w.zustand.text(), "Messung · 10.500 1/min")
            gas["weg"] = True
            w._laufzustand_zeigen(RUN, lv)
            self.assertEqual(a.text(), "GAS WEG!")
            self.assertIn("n Stop 10.800", a.unter.text())
            self.assertEqual(a.stufe, theme.FEHLER)
            self.assertEqual(a.schrift_px(), px_go)                 # feste Schrift, unabhaengig vom Text
            gas["weg"] = False
            w._laufzustand_zeigen(RUN, lv)                          # GAS WEG bleibt seine Zeit stehen
            self.assertEqual(a.text(), "GAS WEG!")
            self.assertEqual(w.zustand.text(), "GAS WEG!")                # auch im Feld neben START
            # mit Ausrollversuch: GAS WEG – KUPPLUNG ZIEHEN! bis das Ausrollen fertig ist
            a.verbergen()
            ctrl.ausrollen, gas["weg"] = True, True
            w._laufzustand_zeigen(RUN, lv)
            self.assertEqual(a.text(), "GAS WEG – KUPPLUNG ZIEHEN!")
            ctrl.state = COAST
            for n in (8000.0, 6000.0, 4000.0):
                lv.n_calc = n
                w._laufzustand_zeigen(COAST, lv)
                self.assertEqual(a.text(), "GAS WEG – KUPPLUNG ZIEHEN!")
                self.assertIn("ausrollen lassen", a.unter.text())
                self.assertEqual(a.schrift_px(), px_go)
            self.assertIn("KUPPLUNG ZIEHEN", w.zustand.text())
            # passt in die Breite des Diagramms, lange Texte brechen um (Schrift bleibt)
            self.assertLessEqual(a.geometry().right(), w.plot.width())
            a.zeige("BITTE GANZ LANGSAM UND VORSICHTIG DIE KUPPLUNG ZIEHEN", "", theme.FEHLER)
            self.assertEqual(a.schrift_px(), px_go)
            self.assertGreater(a.gross.height(), 2 * px_go)
            self.assertTrue(a.gross.wordWrap())
            a.verbergen()
            # Option aus: nur noch GAS WEG wie frueher
            w.anzeige_setzen(mp.ANWEISUNG_GROSS, False)
            self.assertFalse(a.isVisible())
            ctrl.state = WAIT
            w._laufzustand_zeigen(WAIT, lv)
            self.assertFalse(a.isVisible())
            ctrl.state = RUN
            gas["weg"] = False
            w._laufzustand_zeigen(RUN, lv)
            self.assertFalse(a.isVisible())
            gas["weg"] = True
            w._laufzustand_zeigen(RUN, lv)
            self.assertEqual(a.text(), "GAS WEG")
            a.verbergen()
            gas["weg"] = False
            w._laufzustand_zeigen(RUN, lv)
            ctrl.state = COAST                                      # Laufende mit Ausrollen: einmal GAS WEG
            w._laufzustand_zeigen(COAST, lv)
            self.assertEqual(a.text(), "GAS WEG")
            w._gross_verbergen()                                    # START: weg
            self.assertFalse(a.isVisible())
            ctrl.state = DONE
            w._laufzustand_zeigen(DONE, lv)
            self.assertFalse(a.isVisible())
        finally:
            w.ctrl = alt

    def test_laufende_gas_weg_dann_ergebnis(self):
        """Ohne Ausrollversuch: am Laufende GAS WEG! (mind. die Haltezeit), danach das Ergebnis gross; mit
        Ausrollversuch kommt das Ergebnis sofort (Ausrollen fertig). START verwirft ein wartendes Ergebnis."""
        from types import SimpleNamespace
        w = self.w
        ids = self._laeufe(1)
        r = SimpleNamespace(p_max=31.6, n_pmax=9850.0, m_max=22.7, n_mmax=8900.0)
        a, e = w.anweisung, w.ergebnis_gross
        w._laufende_gas_weg()
        self.assertTrue(a.isVisible())
        self.assertEqual(a.text(), "GAS WEG!")
        self.assertEqual(a.stufe, theme.FEHLER)
        self.assertGreater(a.rest_s(), mp.GAS_WEG_HALTEN_S - 0.5)
        w._ergebnis_gross_zeigen(r, ids[0], warten=True)
        self.assertFalse(e.isVisible())                             # GAS WEG steht noch
        self.assertTrue(w._ergebnis_timer.isActive())
        end = time.time() + mp.GAS_WEG_HALTEN_S + 1.5
        while not e.isVisible() and time.time() < end:
            pump(0.05)
        self.assertTrue(e.isVisible())                              # danach das Ergebnis
        self.assertFalse(a.isVisible())
        self.assertIn("31,6 PS", e.werte_text())
        # steht GAS WEG schon seit n Stop: nicht verlaengern
        e.hide()
        a.zeige("GAS WEG!", "n Stop erreicht", theme.FEHLER, 0.3)
        w._laufende_gas_weg()
        self.assertLess(a.rest_s(), 0.35)
        # START waehrend GAS WEG: kein Ergebnis hinterher
        w._ergebnis_gross_zeigen(r, ids[0], warten=True)
        w._gross_verbergen()
        pump(0.5)
        self.assertFalse(e.isVisible())
        self.assertFalse(w._ergebnis_timer.isActive())
        # mit Ausrollversuch (warten=False): sofort, Anweisung weg
        a.zeige("GAS WEG –\nKUPPLUNG ZIEHEN!", "ausrollen lassen", theme.FEHLER)
        w._ergebnis_gross_zeigen(r, ids[0])
        self.assertTrue(e.isVisible())
        self.assertFalse(a.isVisible())
        # Option Anweisungen aus: trotzdem „GAS WEG“ am Laufende
        e.hide()
        w.anzeige_setzen(mp.ANWEISUNG_GROSS, False)
        w._laufende_gas_weg()
        self.assertEqual(a.text(), "GAS WEG")
        w._gross_verbergen()

    def test_seitenleiste_breite_gespeichert(self):
        """Breite der Seitenleiste Läufe: aufgeklappt (nicht angepinnt) am Griff ziehen, gespeichert in
        settings["laeufe_leiste"], gilt auch angepinnt und nach dem Neustart."""
        w = self.w
        L = w.laeufe_leiste
        L._maus_drin = lambda: True                                # offscreen gibt es keinen Mauszeiger
        L.oeffnen()
        self.assertTrue(L.ist_offen() and not L.ist_angepinnt())
        self.assertTrue(L.griff.isVisible())
        links = L.feld.mapToGlobal(QtCore.QPoint(0, 0)).x()
        L.griff.aktiv = True
        L.griff.gezogen.emit(links + 520)
        self.assertTrue(L.ist_beim_ziehen())
        self.assertEqual(L.feld.width(), 520)
        L.griff.aktiv = False
        L.griff.losgelassen.emit()
        self.assertEqual(w.settings["laeufe_leiste"]["breite"], 520)
        from pyst import gui
        self.assertEqual(gui.load_settings()["laeufe_leiste"]["breite"], 520)
        L.set_angepinnt(True)                                      # angepinnt: dieselbe Breite
        pump(0.05)
        self.assertEqual(L.splitter.sizes()[0], 520)
        L.set_angepinnt(False)
        from conftest import close_window
        close_window(self.w, APP)
        self.w = gui.MainWindow()                                  # Neustart
        pump(0.05)
        self.assertEqual(self.w.laeufe_leiste.breite, 520)

    def test_ergebnis_gross(self):
        from types import SimpleNamespace
        w = self.w
        ids = self._laeufe(1)
        r = SimpleNamespace(p_max=31.6, n_pmax=9850.0, m_max=22.7, n_mmax=8900.0)
        self.assertTrue(w.anzeige_an(mp.ERGEBNIS_GROSS))
        w._ergebnis_gross_zeigen(r, ids[0])
        e = w.ergebnis_gross
        self.assertTrue(e.isVisible())
        self.assertIn("31,6 PS bei 9.850 1/min", e.werte_text())
        self.assertIn("22,7 Nm bei 8.900 1/min", e.werte_text())
        self.assertIn("Lauf", e.kopf.text())
        self.assertIn("Serie, HD 135", e.kopf.text())
        self.assertEqual(e.farbe, w._run_color(w.db.run(ids[0])))
        w._esc()                                                    # Esc blendet aus (kein Abbruch)
        self.assertFalse(e.isVisible())
        w._ergebnis_gross_zeigen(r, None)                           # nicht gespeichert: Farbe der Live-Kurve
        self.assertIn("nicht gespeichert", e.kopf.text())
        from PySide6 import QtCore, QtTest
        QtTest.QTest.mouseClick(e, QtCore.Qt.LeftButton)            # Klick blendet aus
        self.assertFalse(e.isVisible())
        w._ergebnis_gross_zeigen(r, ids[0])
        w._gross_verbergen()                                        # START blendet aus
        self.assertFalse(e.isVisible())
        w.anzeige_setzen(mp.ERGEBNIS_GROSS, False)
        w._ergebnis_gross_zeigen(r, ids[0])
        self.assertFalse(e.isVisible())

    def test_anzeige_optionen_schalter_und_menue(self):
        """Schalter unter Einstellungen › Rolle/Lauf und im Menue Ansicht bleiben gleich, gespeichert in settings."""
        w = self.w
        boxen = w.page_settings.anzeige_boxen
        akt = w.anzeige_aktionen
        self.assertEqual(set(boxen), set(mp.ANZEIGE_OPTIONEN))
        for key, (std, _t, _tip) in mp.ANZEIGE_OPTIONEN.items():
            self.assertEqual(boxen[key].isChecked(), std)
            self.assertEqual(akt[key].isChecked(), std)
        self.assertFalse(w.anzeige_an(mp.ANZEIGE_LEGENDE))
        boxen[mp.ANZEIGE_LEGENDE].setChecked(True)
        self.assertTrue(akt[mp.ANZEIGE_LEGENDE].isChecked())
        self.assertTrue(w.settings[mp.ANZEIGE_LEGENDE])
        akt[mp.ERGEBNIS_GROSS].setChecked(False)
        self.assertFalse(boxen[mp.ERGEBNIS_GROSS].isChecked())
        self.assertFalse(w.settings[mp.ERGEBNIS_GROSS])
        from pyst import gui
        gespeichert = gui.load_settings()
        self.assertTrue(gespeichert[mp.ANZEIGE_LEGENDE])
        self.assertFalse(gespeichert[mp.ERGEBNIS_GROSS])
        texte = [a.text() for a in w.menuBar().actions()[0].menu().actions()]
        self.assertIn("Legende im Diagramm", texte)

    def test_legende_aller_laeufe(self):
        w = self.w
        ids = self._laeufe(2)
        w.db.update_run(ids[0], notiz="HD 140 statt 135")
        w._reload_runs()
        self.assertEqual(w.plot.legende_text(), "")                 # Standard aus
        w.anzeige_setzen(mp.ANZEIGE_LEGENDE, True)
        text = w.plot.legende_text()
        self.assertTrue(w.plot.legende.isVisible())
        self.assertIn("Serie, HD 135", text)
        self.assertIn("HD 140 statt 135", text)
        label = w._run_label(w.db.run(ids[0])).split("  ")[0]
        self.assertIn(label, text)
        w.anzeige_setzen(mp.ANZEIGE_LEGENDE, False)
        self.assertEqual(w.plot.legende_text(), "")

    # ---------------------------------------------------------------- Ø automatisch mitteln
    def _auto_zeilen(self):
        t = self.w.run_tree
        return {t.topLevelItem(i).data(0, mp.AUTO_ROLLE): t.topLevelItem(i) for i in range(t.topLevelItemCount())
                if t.topLevelItem(i).data(0, mp.AUTO_ROLLE)}

    def test_auto_mittel_haken_und_neustart(self):
        """Haken im Kopf der Seitenleiste Läufe neben „＋“ (wieder links, nicht unter „Lauf-Einstellungen …“) und im
        Menue Ansicht, Standard aus, wirkt sofort, gespeichert in settings und nach dem Neustart wieder da."""
        w = self.w
        ids = self._laeufe(3)
        self.assertFalse(w.auto_mittel_an())
        self.assertEqual(w.laufliste.auto.text(), "Ø automatisch mitteln")
        self.assertFalse(w.laufliste.auto.isChecked())
        self.assertEqual(w.laeufe_leiste.kopf.indexOf(w.laufliste.auto), 2)      # gleich neben „＋“
        w._lauf_popup_zeigen()                                                 # nicht mehr in Lauf-Einstellungen
        p = w.lauf_popup
        self.assertFalse(hasattr(p, "auto"))
        self.assertFalse([b for b in p.findChildren(QtWidgets.QCheckBox) if b.text() == mp.AUTO_TEXT])
        self.assertNotIn("DIAGRAMM", [lbl.text() for lbl in p.findChildren(QtWidgets.QLabel)])
        p.hide()
        w.laufliste.auto.setChecked(True)                                      # wirkt sofort
        self.assertTrue(w.settings[mp.AUTO_MITTEL])
        self.assertIn(mp.AUTO_KEY, w.plot.data)
        self.assertEqual(set(w._auto["ids"]), set(ids))
        self.assertTrue(w.auto_aktion.isChecked())                             # Menue Ansicht gleich
        w.auto_aktion.setChecked(False)
        self.assertFalse(w.laufliste.auto.isChecked())
        self.assertNotIn(mp.AUTO_KEY, w.plot.data)
        texte = [a.text() for a in w.menuBar().actions()[0].menu().actions()]
        self.assertIn("Ø automatisch mitteln", texte)
        w.auto_aktion.setChecked(True)                                         # Menue Ansicht
        self.assertTrue(w.laufliste.auto.isChecked())
        self.assertTrue(w.settings[mp.AUTO_MITTEL])
        from pyst import gui
        self.assertTrue(gui.load_settings()[mp.AUTO_MITTEL])
        from conftest import close_window
        close_window(self.w, APP)
        self.w = gui.MainWindow()                                              # Neustart
        pump(0.05)
        self.assertTrue(self.w.auto_mittel_an())
        self.assertTrue(self.w.laufliste.auto.isChecked())
        self.assertTrue(self.w.auto_aktion.isChecked())
        self.w.laufliste.auto.setChecked(False)
        self.assertFalse(self.w.auto_aktion.isChecked())
        self.assertFalse(gui.load_settings()[mp.AUTO_MITTEL])

    def test_auto_mittel_im_diagramm(self):
        """Mittelwert der Läufe im Diagramm mit Setup des neuesten: dick, Band, eigene Zeile, kein Platz belegt;
        neu gerechnet bei Haken, Schloss, neuem Lauf und anderem Setup; Mittelwerte zaehlen nicht."""
        w = self.w
        ids = self._laeufe(6)
        self.assertNotIn(mp.AUTO_KEY, w.plot.data)                               # Standard aus
        self.assertEqual(self._auto_zeilen(), {})
        w.auto_mittel_setzen(True)
        roll = w._plaetze[1]
        self.assertEqual(len(roll), mp.MESSPLAETZE)
        self.assertEqual(set(w._auto["ids"]), set(roll))                         # alle im Diagramm, gleiches Setup
        self.assertEqual(w._auto["erg"].quellen, w._auto["ids"])
        self.assertIn(mp.AUTO_KEY, w.plot.data)
        self.assertIn(mp.AUTO_KEY, w.plot.bands)                                 # Band min–max
        self.assertEqual(w.plot._styles[mp.AUTO_KEY][1], mp.MW_BREITE)           # dick wie ein Mittelwert-Lauf
        self.assertEqual(w._auto["titel"], "Ø 5 Läufe · Serie HD 135 (automatisch)")
        self.assertIn("(automatisch)", w.plot.legende_text())
        self.assertEqual(w._plaetze[0], [])                                      # belegt keinen Platz
        self.assertEqual(len(w._plaetze[1]), mp.MESSPLAETZE)
        z = self._auto_zeilen()
        self.assertEqual(set(z), {"kopf", "zeile"})
        self.assertEqual(w.run_tree.indexOfTopLevelItem(z["kopf"]), 0)          # ganz oben
        self.assertEqual(z["zeile"].text(1), "Ø 5 Läufe · Serie HD 135")
        self.assertEqual(w.run_tree.itemWidget(z["zeile"], 5).text(), "speichern")
        # ERGEBNIS rechts und Werte am Cursor wie bei anderen Kurven
        w.run_tree.clearSelection()
        z["zeile"].setSelected(True)
        self.assertEqual(w.ergebnis_titel.text(), "ERGEBNIS  (Ø automatisch, 5 Läufe)")
        d = w.plot.data[mp.AUTO_KEY]
        self.assertEqual(w.k_pmax.wert.text(), mp.zahl(float(np.nanmax(d["ps"])), 1))
        self.assertIn(mp.AUTO_KEY, w._cursor_namen())
        self.assertIsNotNone(w.lauf_werte_bei(mp.AUTO_KEY, float(d["n"][len(d["n"]) // 2])))
        w._reload_runs()                                                         # Auswahl bleibt
        self.assertTrue(w._auto_zeile_gewaehlt())
        # Haken eines Laufs weg: 4 Quellen
        aus = roll[1]
        w.db.update_run(aus, sichtbar=False)
        w._laeufe[aus].sichtbar = False
        w._redraw_runs()
        self.assertEqual(len(w._auto["ids"]), 4)
        self.assertNotIn(aus, w._auto["ids"])
        w.db.update_run(aus, sichtbar=True)
        # Schloss auf den aeltesten: er kommt ins Diagramm und in den Mittelwert, einer faellt rollierend heraus
        alt = next(i for i in ids if i not in roll)
        w.schloss(alt)
        self.assertIn(alt, w._auto["ids"])
        self.assertEqual(len(w._auto["ids"]), 5)
        # gesperrter Mittelwert-Lauf zaehlt nicht mit
        w.schloss(alt)
        mw = self._mittelwert(ids[:3])
        w.laeufe_hinzufuegen([mw])
        self.assertIn(mw, w.diagramm_laeufe())
        self.assertNotIn(mw, w._auto["ids"])
        self.assertEqual(len(w._auto["ids"]), 4)
        w.schloss(mw)
        w.db.delete_run(mw, delete_files=True)          # gemeinsame Datenbank: test_laeufe_dialog zaehlt Mittelwerte
        # neuer Lauf mit demselben Setup: neu gerechnet, neuester zuerst
        f = sorted(x for x in os.listdir(DATA) if x.endswith(".xml"))[0]
        neu = w.db.import_file(os.path.join(DATA, f), vehicle_id=self.vid, setup_id=self.sid)
        w.db.update_run(neu, datum="2099-01-01T12:00:00", sichtbar=True)
        w._reload_runs()                                                         # wie save_current_run
        self.assertEqual(w._auto["ids"][0], neu)
        self.assertEqual(len(w._auto["ids"]), 5)
        # neuester Lauf nur fuer sich mit HD 138: anderes Setup -> allein, kein Mittelwert, Hinweiszeile
        w.db.update_run(neu, setup_werte=[{"feld": "hauptduese", "wert": "138", "vorher": "135"}], nur_lauf=True)
        w._reload_runs()
        self.assertIsNone(w._auto)
        self.assertNotIn(mp.AUTO_KEY, w.plot.data)
        self.assertNotIn(mp.AUTO_KEY, w.plot.bands)
        z = self._auto_zeilen()
        self.assertEqual(set(z), {"kopf", "grund"})
        self.assertIn("gleichem Setup", z["grund"].text(0))
        # anderes Setup fuer die neuesten zwei: Mittelwert aus genau diesen
        sid2 = w.db.save_setup(self.vid, "HD 138", {"hauptduese": "138", "clip": "3", "zzp": "18"})
        zweiter = w._plaetze[1][1]
        for rid in (neu, zweiter):
            w.db.update_run(rid, setup_id=sid2, setup_werte=[], nur_lauf=False)
        w._reload_runs()
        self.assertEqual(w._auto["ids"], [neu, zweiter])
        self.assertEqual(w._auto["sid"], sid2)
        self.assertIn("· HD 138 (automatisch)", w._auto["titel"])
        # Setup-Auswahl wechseln (Filter „aktuelles Setup“): nur noch Laeufe dieses Setups in der Liste
        w.run_filter.setCurrentIndex(2)
        w.setup_combo.setCurrentIndex(w.setup_combo.findData(self.sid))
        self.assertNotIn(neu, w.diagramm_laeufe())
        self.assertEqual(w._auto["sid"], self.sid)
        # Haken aus: weg
        w.auto_mittel_setzen(False)
        self.assertNotIn(mp.AUTO_KEY, w.plot.data)
        self.assertEqual(self._auto_zeilen(), {})

    def test_auto_mittel_speichern(self):
        """„speichern“ an der Zeile: echter Mittelwert-Lauf (wie Auswertung › Mittelwert bilden), über „＋“ wählbar."""
        w = self.w
        ids = self._laeufe(4)
        w.auto_mittel_setzen(True)
        quellen = list(w._auto["ids"])
        self.assertEqual(len(quellen), 4)
        def mittelwerte():
            return [r for r in w.db.runs(mittelwert=True) if r.ist_mittelwert and r.vehicle_id == self.vid]
        self.assertEqual(mittelwerte(), [])                                      # nicht automatisch gespeichert
        knopf = w.run_tree.itemWidget(self._auto_zeilen()["zeile"], 5)
        knopf.click()
        mws = mittelwerte()
        self.assertEqual(len(mws), 1)
        row = mws[0]
        self.assertEqual(row.name, "Ø 4 Läufe · Serie HD 135")
        self.assertEqual(row.quellen, quellen)
        self.assertEqual((row.vehicle_id, row.setup_id), (self.vid, self.sid))
        self.assertEqual(row.werte, {"hauptduese": "135", "clip": "3", "zzp": "18"})
        self.assertTrue(row.notiz.startswith("Quellen: "))
        erg = mittelwert.laden(row)
        np.testing.assert_allclose(erg.werte["ps"], w._auto["erg"].werte["ps"], rtol=1e-5)
        self.assertEqual(mp.band_art(row), "minmax")
        self.assertEqual(w._plaetze[0], [])                                      # nicht gesperrt ins Diagramm
        dlg = mp.LaeufeDialog(w.db, self.vid, w.db.gesperrte_plaetze(self.vid), w._plaetze[1], w)
        gefunden = [dlg.liste.topLevelItem(i).data(0, QtCore.Qt.UserRole) for i in range(dlg.liste.topLevelItemCount())]
        self.assertIn(row.id, gefunden)
        dlg.deleteLater()
        self.assertEqual(w.laeufe_hinzufuegen([row.id]), 1)
        self.assertNotIn(row.id, w._auto["ids"])                                 # gespeicherter Mittelwert zaehlt nicht
        w.auto_mittel_setzen(False)
        self.assertIsNone(w.auto_mittel_speichern())                             # ohne Mittelwert nichts
        self.assertEqual(len(mittelwerte()), 1)
        self.assertEqual(sorted(ids), sorted(quellen))
        w.db.delete_run(row.id, delete_files=True)      # gemeinsame Datenbank: test_laeufe_dialog zaehlt Mittelwerte

    def test_live_plaetze_hinzufuegen_entfernen(self):
        """„＋“ fuegt Live-Plaetze an (hoechstens LIVE_MAX), „Platz entfernen“ im Titel-Menue, je Fahrzeug."""
        from pyst.db import LIVE_MAX
        w = self.w
        self.assertEqual(len(w.live_kacheln), 5)
        self.assertTrue(w.live_platz_hinzufuegen("egt2"))
        self.assertEqual(len(w.live_kacheln), 6)
        self.assertEqual(w.live_kacheln[5].kanal, "egt2")
        self.assertEqual(w.live_kacheln[5].nr.text(), "6")
        self.assertEqual(w.db.live_plaetze(self.vid)[5]["kanal"], "egt2")
        # Menue des Knopfs ＋ und Titel-Menue mit „Platz entfernen“
        m = w.live_plus.menu()
        w._live_plus_menue(m)
        self.assertTrue(any(a.text().startswith("EGT 2") for a in m.actions()))
        k = w.live_kacheln[0]
        k._menue_fuellen()
        weg = next(a for a in k.menue.actions() if a.text() == "Platz entfernen")
        weg.trigger()
        pump(0.05)
        self.assertEqual(len(w.live_kacheln), 5)
        self.assertEqual(w.live_kacheln[0].kanal, "ecu:coolant")    # der Rest rueckt auf
        while w.live_platz_hinzufuegen("rate"):
            pass
        self.assertEqual(len(w.live_kacheln), LIVE_MAX)
        self.assertFalse(w.live_plus.isVisibleTo(w.live_flaeche.widget()))
        # rollt, Drehzahl und UMGEBUNG bleiben ausserhalb des Rollbereichs
        self.assertFalse(w.live_flaeche.widget().isAncestorOf(w.k_drehzahl))
        self.assertTrue(w.live_flaeche.widget().isAncestorOf(w.live_kacheln[-1]))
        while w.live_platz_entfernen(0):
            pass
        self.assertEqual(w.live_kacheln, [])
        self.assertEqual(w.db.live_plaetze(self.vid), [])
        self.assertTrue(w.live_plus.isVisibleTo(w.live_flaeche.widget()))
        pump(0.05)

    def test_rechte_spalte_kompakt(self):
        """Bis 4 Plaetze eine Spalte, darueber zweispaltig und kompakt (MINI); Drehzahl bleibt gross.
        Bei 1280×800 sind 8 Plaetze ganz sichtbar; UMGEBUNG eine Zeile, ERGEBNIS Pmax | Mmax nebeneinander."""
        from pyst.kacheln import GROSS, MINI
        w = self.w
        w.resize(1280, 800)
        w.show()
        pump(0.2)
        self.assertEqual(w._live_groesse(), MINI)                    # 5 Standardplaetze
        self.assertEqual(w.live_lay.getItemPosition(w.live_lay.indexOf(w.live_kacheln[1]))[:2], (0, 1))
        self.assertEqual(w.k_drehzahl.groesse, GROSS)
        while len(w.live_kacheln) > 4:
            w.live_platz_entfernen(len(w.live_kacheln) - 1)
        self.assertNotEqual(w._live_groesse(), MINI)
        self.assertEqual(w.live_lay.getItemPosition(w.live_lay.indexOf(w.live_kacheln[1]))[:2], (1, 0))
        for k in ("egt2", "rate", "ratio", "lam"):
            w.live_platz_hinzufuegen(k)
        pump(0.3)
        w.live_flaeche.verticalScrollBar().setValue(0)
        pump(0.05)
        vp = w.live_flaeche.viewport()
        ganz = [vp.rect().contains(QtCore.QRect(k.mapTo(vp, QtCore.QPoint(0, 0)), k.size())) for k in w.live_kacheln]
        self.assertEqual(len(ganz), 8)
        self.assertTrue(all(ganz), ganz)
        self.assertTrue(all(k.groesse == MINI and not k.balken.skala for k in w.live_kacheln))
        # UMGEBUNG eine Zeile, ERGEBNIS nebeneinander mit „@ Drehzahl“ klein darunter
        w.klima.set("22,3", "1.010", "vor 20 s")
        self.assertEqual(w.klima.text(), "T 22,3 °C · p 1.010 mbar · vor 20 s")
        self.assertLess(w.klima.height(), 40)
        w.k_pmax.set("Pmax @ 10.420", "32,0")
        self.assertEqual((w.k_pmax.titel.text(), w.k_pmax.bei.text()), ("Pmax", "@ 10.420"))
        self.assertEqual(w.k_pmax.y(), w.k_mmax.y())
        w.hide()


if __name__ == "__main__":
    unittest.main()
