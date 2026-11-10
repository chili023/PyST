"""Tasten der Funk-Zange: Setup, Marker-Nummern, Neuer Lauf, Übersetzung, Reiterstruktur (Simulator, offscreen).

    python -m pytest tests/test_tasten.py

Mit PYST_SCREENSHOTS=<Ordner> legt der Oberflaechentest Bildschirmfotos ab.
"""
import importlib
import json
import os
import sys
import tempfile
import time
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from conftest import close_window  # noqa: E402
from pyst import basis, storage, tasten, zange  # noqa: E402
from pyst.tasten import TastenSync, UebersetzungMessung, auswerten  # noqa: E402


class Setup(unittest.TestCase):
    def test_wert_wie_firmware(self):
        self.assertEqual(tasten.packen(tasten.STANDARD), 0x504321)              # ZT_STANDARD
        self.assertEqual(tasten.entpacken(0x504321), tasten.STANDARD)
        s = dict(tasten.STANDARD, t4_kurz="marker", t1_kurz="lambda")
        self.assertEqual(tasten.packen(s), 0x514325)
        self.assertEqual(tasten.entpacken(tasten.packen(s)), s)

    def test_standard_laut_festlegung(self):
        self.assertEqual(tasten.STANDARD, {"t1_kurz": "marker", "t2_kurz": "aufnahme", "t3_kurz": "lauf",
                                           "t3_lang": "uebersetzung", "t4_kurz": "nichts", "t4_lang": "lambda"})

    def test_laden_ist_robust(self):
        self.assertEqual(tasten.laden(None), tasten.STANDARD)
        self.assertEqual(tasten.laden("quatsch"), tasten.STANDARD)
        s = tasten.laden({"t4_kurz": "marker", "t1_kurz": "gibtsnicht", "neu": "lauf"})
        self.assertEqual(s, dict(tasten.STANDARD, t4_kurz="marker"))

    def test_doppelbelegung(self):
        self.assertEqual(tasten.doppelt(tasten.STANDARD), [])
        s = dict(tasten.STANDARD, t4_kurz="marker", t2_kurz="marker")           # Marker darf mehrfach
        self.assertEqual(tasten.doppelt(s), [])
        s = dict(tasten.STANDARD, t4_kurz="lauf", t1_kurz="nichts")
        self.assertEqual(tasten.doppelt(s), [("lauf", ["t3_kurz", "t4_kurz"])])
        self.assertIn("T3 kurz und T4 kurz", tasten.doppelt_text(s))


class Sync(unittest.TestCase):
    def test_verbinden_schickt_einmal(self):
        s = TastenSync(7)
        self.assertIsNone(s.faellig(0))                     # keine Zange: nichts senden
        s.zange(True)
        self.assertEqual(s.faellig(0), 7)
        self.assertIsNone(s.faellig(0))                     # unterwegs
        s.antwort(0, 7, ["#OK;ZANGE;SET;tasten=7"])
        self.assertEqual(s.zustand, s.OK)
        s.zange(True)                                       # Statusleiste meldet zyklisch
        self.assertIsNone(s.faellig(1))
        self.assertIn("bestätigt", s.text()[0])

    def test_jedes_neue_verbinden_und_neustart(self):
        s = TastenSync(7)
        s.zange(True)
        s.faellig(0)
        s.antwort(0, 7, ["#OK"])
        s.zange(False)
        self.assertEqual(s.zustand, s.AUS)
        s.zange(True)
        self.assertEqual(s.faellig(1), 7)
        s.antwort(1, 7, ["#OK"])
        s.neu()                                             # Zange neu gestartet, nie als verloren gemeldet
        self.assertEqual(s.faellig(2), 7)

    def test_aenderung_im_menue(self):
        s = TastenSync(7)
        s.setzen(8)                                         # ohne Zange nur merken
        self.assertIsNone(s.faellig(0))
        s.zange(True)
        self.assertEqual(s.faellig(0), 8)
        s.setzen(9)                                         # Änderung, während die alte Antwort unterwegs ist
        self.assertEqual(s.faellig(0), 9)
        s.antwort(0, 8, ["#OK"])
        self.assertEqual(s.zustand, s.OFFEN)
        self.assertEqual(s.faellig(0), 9)
        s.antwort(0, 9, ["#OK"])
        self.assertEqual(s.zustand, s.OK)

    def test_wiederholen_und_aufgeben(self):
        s = TastenSync(7)
        s.zange(True)
        now = 0.0
        for _ in range(s.VERSUCHE - 1):
            self.assertEqual(s.faellig(now), 7)
            s.antwort(now, 7, ["#ERR;ZANGE;belegt"])
            self.assertIsNone(s.faellig(now + 0.1))
            now += s.WIEDERHOLEN_S
        self.assertEqual(s.faellig(now), 7)
        s.antwort(now, 7, [])
        self.assertEqual(s.zustand, s.FEHLER)
        self.assertIsNone(s.faellig(now + 100))
        self.assertIn("nicht bestätigt", s.text()[0])

    def test_alte_firmware(self):
        s = TastenSync(7)
        s.zange(True)
        s.faellig(0)
        s.antwort(0, 7, ["#ERR;ZANGE;unbekannt"])
        self.assertEqual(s.zustand, s.ALT)
        self.assertIsNone(s.faellig(100))
        self.assertIn("0.1.3", s.text()[0])


class Sperren(unittest.TestCase):
    def test_ruhe(self):
        self.assertEqual(tasten.sperre(True, False, False, False), "")

    def test_jede_sperre_greift(self):
        self.assertIn("nicht verbunden", tasten.sperre(False, False, False, False))
        self.assertIn("Lauf", tasten.sperre(True, True, False, False))
        self.assertIn("Oszi", tasten.sperre(True, False, True, False))
        self.assertIn("geflasht", tasten.sperre(True, False, False, True))
        self.assertIn("geflasht", tasten.sperre(False, False, False, True))     # beim Flashen ist der Port weg
        self.assertIn("Dialog", tasten.sperre(True, False, False, False, dialog=True))
        self.assertIn("Prüfung", tasten.sperre(True, False, False, False, pruefung=True))


def proben(n=100, motor=4110.0, rolle=600.0, rausch=0.002, seed=2):
    r = np.random.default_rng(seed)
    return motor * (1 + r.normal(0, rausch, n)), rolle * (1 + r.normal(0, rausch, n))


class Rechnung(unittest.TestCase):
    def test_stabil(self):
        wert, fehler = auswerten(*proben(), bisher=6.5)
        self.assertEqual(fehler, "")
        self.assertAlmostEqual(wert, 6.85, delta=0.01)

    def test_getrimmter_mittelwert_wie_dialog(self):
        from pyst import physics
        nm, nr = proben()
        nm[:5] *= 1.02                                      # einzelne Ausreisser fallen im Trimmen weg
        self.assertEqual(auswerten(nm, nr)[0], physics.trimmed_ratio(nm, nr))

    def test_instabile_drehzahl(self):
        nm, nr = proben()
        rampe = np.linspace(0.85, 1.15, len(nm))            # Fahrer beschleunigt: Übersetzung stimmt, Drehzahl nicht
        wert, fehler = auswerten(nm * rampe, nr * rampe)
        self.assertIsNone(wert)
        self.assertIn("nicht stabil", fehler)

    def test_streuung(self):
        wert, fehler = auswerten(*proben(rausch=0.05))
        self.assertIsNone(wert)
        self.assertIn("nicht stabil", fehler)

    def test_kein_signal(self):
        nm, nr = proben()
        self.assertIsNone(auswerten(nm * 0, nr)[0])
        self.assertIsNone(auswerten(nm, nr * 0)[0])
        nm[:30] = 0                                         # 30 % Aussetzer
        self.assertIn("keine Drehzahl", auswerten(nm, nr)[1])
        self.assertIn("wenige", auswerten(nm[:5], nr[:5])[1])

    def test_doppelt_zaehlend_gegen_bisher(self):
        nm, nr = proben()
        wert, fehler = auswerten(nm * 2, nr, bisher=6.85)
        self.assertIsNone(wert)
        self.assertIn("doppelt", fehler)
        self.assertIsNone(auswerten(nm / 2, nr, bisher=6.85)[0])
        self.assertIsNotNone(auswerten(nm * 1.3, nr, bisher=6.85)[0])    # andere Übersetzung ist in Ordnung

    def test_doppelt_zaehlend_gegen_ecu(self):
        nm, nr = proben()
        wert, fehler = auswerten(nm * 2, nr, n_ecu=nm)
        self.assertIsNone(wert)
        self.assertIn("rusEFI", fehler)
        self.assertIsNotNone(auswerten(nm, nr, n_ecu=nm * 1.01)[0])
        self.assertIsNotNone(auswerten(nm, nr, n_ecu=nm * np.nan)[0])    # ECU liefert nichts: Prüfung entfällt


class Ablauf(unittest.TestCase):
    def messen(self, m, nm, nr):
        self.assertEqual(m.taste(0.0, 6.5), ("start", None))
        self.assertTrue(m.aktiv)
        for a, b in zip(nm, nr):
            m.probe(a, b)
        self.assertIsNone(m.tick(4.9))
        return m.tick(5.0)

    def test_zweites_druecken_uebernimmt(self):
        m = UebersetzungMessung()
        self.assertEqual(self.messen(m, *proben()).art, "fertig")
        self.assertEqual(m.zustand, m.WARTET)
        was, wert = m.taste(10.0, 6.5)
        self.assertEqual(was, "uebernehmen")
        self.assertAlmostEqual(wert, 6.85, delta=0.01)
        self.assertFalse(m.aktiv)

    def test_ohne_bestaetigung_nichts(self):
        m = UebersetzungMessung()
        self.messen(m, *proben())
        self.assertIsNone(m.tick(34.9))
        ende = m.tick(35.0)
        self.assertEqual(ende.art, "verworfen")
        self.assertFalse(m.aktiv)
        self.assertEqual(m.taste(36.0, 6.5), ("start", None))       # nächster Druck misst neu, übernimmt nichts

    def test_druck_waehrend_der_messung_uebernimmt_nicht(self):
        m = UebersetzungMessung()
        m.taste(0.0, 6.5)
        self.assertEqual(m.taste(1.0, 6.5), ("laeuft", None))
        self.assertEqual(m.zustand, m.MISST)

    def test_fehler_laesst_nichts_stehen(self):
        m = UebersetzungMessung()
        e = self.messen(m, *proben(rausch=0.05))
        self.assertEqual(e.art, "fehler")
        self.assertIsNone(m.wert)
        self.assertFalse(m.aktiv)
        self.assertEqual(m.taste(6.0, 6.5), ("start", None))

    def test_proben_nur_beim_messen(self):
        m = UebersetzungMessung()
        m.probe(1, 1)
        self.assertEqual(self.messen(m, *proben(n=9)).art, "fehler")     # alte Probe zählt nicht mit


class Marker(unittest.TestCase):
    def ev(self, *args):
        return basis.BasisEvent(0.0, "MARK", list(args))

    def test_text(self):
        self.assertEqual(self.ev("zange", "T1").marker_text(3), "Marker 3")
        self.assertEqual(self.ev("zange", "fern").marker_text(1), "Marker 1")
        self.assertEqual(self.ev("zange", "T2/Z2").marker_text(2), "Marker 2 (Zange 2)")
        self.assertEqual(self.ev("pyst", "Marker").marker_text(4), "Marker 4")
        self.assertEqual(self.ev("basis", "Kabel").marker_text(1), "Marker Basis: Kabel")     # Text mit Inhalt bleibt

    def test_lauf_zaehlt_je_lauf(self):
        with tempfile.TemporaryDirectory() as d:
            evs = [basis.BasisEvent(0.0, "MARK", ["zange", t], 10.0 + i) for i, t in enumerate(("T1", "T3", "T1"))]
            evs.insert(1, basis.BasisEvent(0.0, "ZANGE", ["verloren"], 10.5))
            self.assertEqual(storage.write_side_events(d, 10.0, evs), (3, 1))
            with open(os.path.join(d, "marker.csv"), encoding="utf-8") as fh:
                zeilen = fh.read().splitlines()
            self.assertEqual(zeilen[1:], ["0.000;Marker 1", "1.000;Marker 2", "2.000;Marker 3"])

    def test_zangen_log_alte_tasten_bleiben_lesbar(self):
        rows = ["L;1;B;1000;1", "L;2;F;2000;0;0;3000", "L;3;M;3000;1", "L;4;M;4000;4", "L;5;M;5000;10", "L;6;M;6000;7"]
        entries = [basis.parse_log_line(x) for x in rows]
        from pyst import physics
        res = zange.log_to_rows(entries, physics.DynoParams())
        self.assertEqual([t for _x, t in res["markers"]], ["Marker 1 (Taste T1)", "Marker 2 (Taste T4)",
                                                           "Marker 3 (fern)", "Marker 4 (Art 7)"])

    def test_ztaste_text(self):
        ev = basis.parse_event("E;100;ZTASTE;lauf;1")
        self.assertFalse(ev.is_marker)
        self.assertIn("Neuer Lauf", ev.text)
        self.assertIn("neu gestartet", basis.parse_event("E;100;ZANGE;neustart;1").text)


class Gui(unittest.TestCase):
    """Das ganze Hauptfenster gegen SIM:basis und SIM:zange."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = {k: os.environ.get(k) for k in ("HOME", "QT_QPA_PLATFORM")}
        os.environ["HOME"] = self.tmp.name
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6 import QtWidgets
        self.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        from pyst import gui
        importlib.reload(gui)
        self.gui = gui
        self.w = gui.MainWindow()
        self.w.resize(1480, 900)
        self.w.show()
        self.shots = os.environ.get("PYST_SCREENSHOTS", "")

    def tearDown(self):
        close_window(self.w, self.app)
        self.w = None
        self.tmp.cleanup()
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def run_gui(self, sec, cond=None):
        end = time.time() + sec
        while time.time() < end:
            self.app.processEvents()
            if cond is not None and cond():
                return True
            time.sleep(0.01)
        return cond is None

    def shot(self, name):
        if self.shots:
            os.makedirs(self.shots, exist_ok=True)
            self.w.grab().save(os.path.join(self.shots, name))

    def verbinden(self, port="SIM:basis"):
        w = self.w
        w.page_settings.port_combo.setEditText(port)
        self.assertTrue(w.on_connect())
        w.link.ser.settings["sim_zange_mark"] = "0"         # keine zufälligen Tasten
        if port == "SIM:basis":
            self.assertTrue(self.run_gui(5, lambda: w.page_tasten.sync.zustand == TastenSync.OK))
        return w.link.ser

    def hold(self, ser):
        """Simulator hält die Drehzahl, statt Vollgas zu geben."""
        ser.engine.reset()
        ser.engine.t_phase = -1e6

    # ------------------------------------------------------------------ Reiter
    def test_reiterstruktur(self):
        w = self.w
        top = [w.tabs.tabText(i) for i in range(w.tabs.count())]
        self.assertEqual(top, ["Messen", "Fahrzeuge && Setups", "Auswertung", "Oszi / Dauermessung", "Einstellungen"])
        sub = [w.settings_tabs.tabText(i) for i in range(w.settings_tabs.count())]
        self.assertEqual(sub, ["Rolle/Lauf", "Verbindung", "Zange", "Lambda", "Analogeingänge", "ECU",
                               "Software && Firmware", "WiFi"])
        s = w.page_settings
        # Entwurf 08.11.2026: keine Reiter in den Unterreitern, die Teile stecken als Gruppen in ihren Seiten
        self.assertEqual([type(w.settings_tabs.widget(i)) for i in range(8) if isinstance(
            w.settings_tabs.widget(i), type(w.settings_tabs))], [])
        self.assertIs(w.settings_tabs.widget(0), s)
        self.assertIs(w.settings_tabs.widget(1), s.conn_page)
        self.assertIs(w.settings_tabs.widget(2), w.page_hw)
        self.assertIs(w.settings_tabs.widget(4), w.page_analog)
        self.assertIs(w.settings_tabs.widget(5), w.page_rusefi)
        self.assertIs(w.settings_tabs.widget(6), w.page_flash)
        self.assertTrue(w.page_hw.isAncestorOf(w.page_tasten) and w.page_hw.isAncestorOf(w.page_pruef_zange))
        self.assertTrue(s.conn_page.isAncestorOf(w.page_pruef) and s.conn_page.isAncestorOf(w.dev_settings_btn))
        self.assertTrue(w.page_lambda.isAncestorOf(w.page_hw.box_lambda))
        self.assertTrue(w.page_lambda.isAncestorOf(s.lambda_radio) and w.page_lambda.isAncestorOf(w.page_pruef_lambda))
        self.assertTrue(w.page_wifi.isAncestorOf(w.page_hw.box_net) and w.page_wifi.isAncestorOf(w.page_hw.box_plugs))
        self.assertTrue(s.isAncestorOf(w.page_kalib) and s.isAncestorOf(s.ratio_btn) and s.isAncestorOf(s.start_nachfrage))
        self.assertTrue(s.conn_page.isAncestorOf(s.port_combo) and s.conn_page.isAncestorOf(s.connect_btn))
        self.assertFalse(s.isAncestorOf(s.port_combo))
        self.assertTrue(s.isAncestorOf(s.fields["inkr"]))

    def test_sichtbarkeit_folgt_den_unterreitern(self):
        w = self.w
        w.show_page(w.page_flash)
        self.run_gui(0.05)
        self.assertTrue(w.page_flash.isVisible())
        self.assertFalse(w.page_hw.isVisible())
        w.show_page(w.page_hw)
        self.run_gui(0.05)
        self.assertTrue(w.page_hw.isVisible())
        self.assertFalse(w.page_flash.isVisible())
        w.show_page(w.page_oszi)                            # Unterreiter steht auf Zange, Reiter woanders
        self.run_gui(0.05)
        self.assertFalse(w.page_hw.isVisible())
        self.assertEqual(w.tabs.currentWidget(), w.page_oszi)

    def test_f3_und_statusleiste_ueberall(self):
        from PySide6 import QtCore
        from PySide6.QtTest import QTest
        w = self.w
        self.verbinden()
        zange = w.statusleiste.feld("zange")
        self.assertTrue(self.run_gui(5, lambda: zange.stufe == "ok" and "Zange 1" in zange.text()), zange.text())
        self.assertIn("Lambda", self._warte_lambda())
        w.activateWindow()
        for page in (w.page_oszi, w.page_tasten, w.page_flash, w.page_hw):
            w.show_page(page)
            self.run_gui(0.4)                               # hw_page holt die Antwort im 250-ms-Takt ab
            n = len(w.page_oszi.markers)
            QTest.keyClick(w, QtCore.Qt.Key_F3)
            self.assertTrue(self.run_gui(3, lambda: len(w.page_oszi.markers) == n + 1), page)
            self.assertEqual(zange.stufe, "ok")
            self.assertTrue(zange.isVisible() and w.statusleiste.feld("ecu").isVisible())

    def _warte_lambda(self):
        ecu = self.w.statusleiste.feld("ecu")
        self.run_gui(3, lambda: "Lambda" in ecu.text())
        return ecu.text()

    def test_hooks(self):
        w = self.w
        self.verbinden()
        w.show_page(w.page_pruef)
        self.run_gui(0.4)
        self.assertTrue(w.page_pruef.b_pair.isEnabled())
        w.page_pruef.pair()
        self.assertIn("Kopplungsfenster", w.page_hw.z_msg.text())
        seen = []
        w._connect_port = seen.append                       # Signal hängt an der alten Methode
        w.page_hw.connect_port.disconnect()
        w.page_hw.connect_port.connect(w._connect_port)
        w.page_hw._connect_tcp()
        self.assertTrue(seen and seen[0].startswith("tcp://"))

    def test_layout_speichern(self):
        w = self.w
        w.show_page(w.page_tasten)
        w.save_window_layout()
        f = w.settings["fenster"]
        self.assertEqual(f["seite"], "zange")                        # Tasten stecken im Unterreiter Zange
        w.show_page(w.page_oszi)
        w.settings_tabs.setCurrentIndex(0)
        w.restore_window_layout()
        self.assertIs(w.current_page(), w.page_hw)
        self.assertIs(w.tabs.currentWidget(), w.settings_tabs)
        # gespeichert von der Version ohne Unterreiter
        for alt, page in ((6, w.page_flash), (4, w.page_hw), (5, w.page_settings.conn_page), (8, w.page_settings),
                          (7, w.page_rusefi), (2, w.viewer)):
            w.show_page(w.page_db)
            w.settings["fenster"] = {"reiter": alt}
            w.restore_window_layout()
            self.assertIs(w.current_page(), page, alt)
        # gespeichert von PyST 1.4 (Reiter ECU und Kalibrierung oben, Einstellungen mit sechs Unterreitern)
        for f, page in (({"reiter_neu": [6, 3], "reiter_kalib": True}, w.page_hw),
                        ({"reiter_neu": [5, 0], "reiter_kalib": True}, w.page_settings),
                        ({"reiter_neu": [4, 0], "reiter_kalib": True}, w.page_rusefi),
                        ({"reiter_neu": [6, 1], "reiter_kalib": True}, w.page_settings.conn_page)):
            w.show_page(w.page_db)
            w.settings["fenster"] = f
            w.restore_window_layout()
            self.assertIs(w.current_page(), page, f)

    # ------------------------------------------------------------------ Setup
    def test_setup_senden(self):
        w = self.w
        p = w.page_tasten
        ser = self.verbinden()
        self.assertEqual(ser.z_tasten, 0x504321)
        self.assertIn("bestätigt", p.status.text())
        # Änderung im Menü geht sofort hinaus und bleibt in den Einstellungen
        p.combos["t4_kurz"].setCurrentIndex(p.combos["t4_kurz"].findData("marker"))
        self.assertTrue(self.run_gui(3, lambda: ser.z_tasten == 0x514321 and p.sync.zustand == TastenSync.OK))
        self.assertEqual(w.settings["zange_tasten"]["t4_kurz"], "marker")
        with open(self.gui.SETTINGS_FILE, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["zange_tasten"]["t4_kurz"], "marker")
        self.assertEqual(p.doppelt.text(), "")
        # Neustart ohne „verloren“: Zange steht auf Standard, PyST schickt nach
        ser.sim_zange_neustart()
        self.assertEqual(ser.z_tasten, 0x504321)
        self.assertTrue(self.run_gui(3, lambda: ser.z_tasten == 0x514321))
        # verloren und wieder da
        ser._zange_lost(None)
        ser.z_tasten = 0x504321
        self.assertTrue(self.run_gui(4, lambda: p.sync.zustand == TastenSync.AUS))
        self.assertIn("Keine Zange", p.status.text())
        ser._zange_back()
        self.assertTrue(self.run_gui(4, lambda: ser.z_tasten == 0x514321))
        # Doppelbelegung: Hinweis, gespeichert wird trotzdem
        p.combos["t1_kurz"].setCurrentIndex(p.combos["t1_kurz"].findData("lambda"))
        self.assertIn("Lambda ein/aus", p.doppelt.text())
        w.show_page(p)
        self.run_gui(0.3)
        self.shot("tasten.png")
        p.b_standard.click()
        self.assertEqual(p.setup, tasten.STANDARD)
        self.assertEqual(p.doppelt.text(), "")
        self.assertTrue(self.run_gui(3, lambda: ser.z_tasten == 0x504321))

    def test_setup_bleibt_ueber_neustart_von_pyst(self):
        w = self.w
        p = w.page_tasten
        p.combos["t3_kurz"].setCurrentIndex(p.combos["t3_kurz"].findData("nichts"))
        w2 = self.gui.MainWindow()
        try:
            self.assertEqual(w2.page_tasten.setup["t3_kurz"], "nichts")
            self.assertEqual(w2.page_tasten.combos["t3_kurz"].currentData(), "nichts")
        finally:
            w2.close()

    def test_setup_per_usb(self):
        w = self.w
        w.page_tasten.combos["t4_kurz"].setCurrentIndex(1)
        ser = self.verbinden("SIM:zange")
        w._warn_box.close()
        self.assertTrue(self.run_gui(3, lambda: ser.settings["tasten"] == str(0x514321)))
        self.assertTrue(self.run_gui(3, lambda: w.page_tasten.sync.zustand == TastenSync.OK))

    def test_alte_firmware_wird_gemeldet(self):
        w = self.w
        ser = self.verbinden()
        ser_diag = ser._diag_command

        def alt(line):
            if "tasten" in line:
                ser._out("#ERR;ZANGE;unbekannt\r\n")
            else:
                ser_diag(line)
        ser._diag_command = alt
        ser.sim_zange_neustart()
        self.assertTrue(self.run_gui(3, lambda: w.page_tasten.sync.zustand == TastenSync.ALT))
        self.assertIn("zu alt", w.page_tasten.status.text())

    def test_aktion_folgt_dem_setup(self):
        w = self.w
        ser = self.verbinden()
        p = w.page_tasten
        # Lambda auf T1 kurz: geht genauso wie auf T4 lang
        p.combos["t1_kurz"].setCurrentIndex(p.combos["t1_kurz"].findData("lambda"))
        self.assertTrue(self.run_gui(3, lambda: ser.z_tasten & 0xF == 5))
        ser.sim_taste("t1_kurz")
        self.assertTrue(self.run_gui(3, lambda: ser._lam_on()))
        # Blitzmodus sticht das Setup: T1 kurz ist Marker, T3 kurz φ
        ser.sim_taste("t2_lang")
        n = len(w.page_oszi.markers)
        ser.sim_taste("t1_kurz")
        self.assertTrue(self.run_gui(3, lambda: len(w.page_oszi.markers) == n + 1))
        self.assertTrue(ser._lam_on())
        ser.sim_taste("t3_kurz")
        self.run_gui(0.3)
        self.assertEqual(ser.z_settings["blitz_phi_x10"], "-5")
        self.assertEqual(ser.z_led, [])                     # kein Neuer Lauf
        self.assertNotIn(w.ctrl.state, (self.gui.WAIT, self.gui.RUN))

    # ------------------------------------------------------------------ Marker
    def test_marker_nummern(self):
        w = self.w
        ser = self.verbinden()
        o = w.page_oszi
        # Oszi: Aufzeichnung beginnt bei 1
        ser.sim_taste("t1_kurz")
        self.assertTrue(self.run_gui(3, lambda: len(o.markers) >= 1))
        o.rec_btn.setChecked(True)
        self.run_gui(0.5)
        self.assertEqual(o.markers, [])
        for _ in range(2):
            ser.sim_taste("t1_kurz")
        self.assertTrue(self.run_gui(3, lambda: len(o.markers) == 2))
        self.assertEqual([t for _x, t in o.markers], ["Marker 1", "Marker 2"])
        o.rec_btn.setChecked(False)
        run = storage.load_any(self._ordner(w.db.runs()[0].path))
        self.assertEqual([t for _x, t in run["markers"]], ["Marker 1", "Marker 2"])
        # Lauf: je Lauf von vorn, Auswertung zeigt denselben Text
        for lauf in range(2):
            w.on_start()
            ser.sim_taste("t1_kurz")
            ser.sim_taste("t1_kurz")
            self.assertTrue(self.run_gui(3, lambda: sum(e.is_marker for e in w.ctrl.basis_events) == 2))
            if lauf == 0:
                self.assertEqual(w.statusBar().currentMessage(), "Marker 2")
            self.assertTrue(self.run_gui(60, lambda: w.ctrl.state == self.gui.DONE and w._done_handled))
            row = w.db.runs()[0]
            run = storage.load_any(self._ordner(row.path))
            self.assertEqual([t for _x, t in run["markers"]], ["Marker 1", "Marker 2"])
            self.assertEqual([t for _x, t in w._viewer_channels(row.id)[2]][:2], ["Marker 1", "Marker 2"])

    @staticmethod
    def _ordner(path):
        return path if os.path.isdir(path) else os.path.dirname(path)

    # ------------------------------------------------------------------ Neuer Lauf
    def test_neuer_lauf(self):
        w = self.w
        ser = self.verbinden()
        w.show_page(w.page_tasten)
        ser.sim_taste("t3_kurz")
        self.assertTrue(self.run_gui(3, lambda: w.ctrl.state in (self.gui.WAIT, self.gui.RUN)))
        self.assertEqual(w.tabs.currentIndex(), 0)
        self.assertTrue(self.run_gui(3, lambda: tasten.LED_OK in ser.z_led))
        # zweiter Druck startet nichts neu und bricht nichts ab
        frames = w.ctrl.frames
        ser.z_led.clear()
        ser.sim_taste("t3_kurz")
        self.assertTrue(self.run_gui(3, lambda: ser.z_led == [tasten.LED_FEHLER]))
        self.assertIs(w.ctrl.frames, frames)
        self.assertIn(w.ctrl.state, (self.gui.WAIT, self.gui.RUN))
        self.assertIn("läuft schon", w.band.text())
        self.assertFalse(w.band.isHidden())
        self.shot("hinweis_lauf.png")
        w.on_abort()

    def test_neuer_lauf_gesperrt(self):
        w = self.w
        ser = self.verbinden()
        # Oszi-Aufnahme
        w.page_oszi.rec_btn.setChecked(True)
        self.run_gui(1.0)                                   # leere Aufzeichnung lässt sich nicht ablegen
        ser.sim_taste("t3_kurz")
        self.assertTrue(self.run_gui(3, lambda: ser.z_led == [tasten.LED_FEHLER]))
        self.assertIn("Oszi", w.band.text())
        self.assertNotIn(w.ctrl.state, (self.gui.WAIT, self.gui.RUN))
        w.page_oszi.rec_btn.setChecked(False)
        # Flashen
        ser.z_led.clear()
        w.page_flash.worker = object()
        ser.sim_taste("t3_kurz")
        self.assertTrue(self.run_gui(3, lambda: ser.z_led == [tasten.LED_FEHLER]))
        self.assertIn("geflasht", w.band.text())
        w.page_flash.worker = None
        # unmögliche Einstellungen: Hinweis statt Dialog
        ser.z_led.clear()
        w.page_settings.fields["n_min"].setValue(9000)
        w.page_settings.fields["n_stop"].setValue(100)
        ser.sim_taste("t3_kurz")
        self.assertTrue(self.run_gui(3, lambda: ser.z_led == [tasten.LED_FEHLER]))
        self.assertIsNone(self.app.activeModalWidget())
        self.assertNotIn(w.ctrl.state, (self.gui.WAIT, self.gui.RUN))

    # ------------------------------------------------------------------ Übersetzung
    def test_uebersetzung(self):
        w = self.w
        ser = self.verbinden()
        self.hold(ser)
        w.ratio_mess.mess_s = 1.0
        ratio = w.page_settings.fields["ratio"]
        ratio.setValue(6.5)
        ser.sim_taste("t3_lang")
        self.assertTrue(self.run_gui(3, lambda: ser.z_led == [tasten.LED_LAEUFT]))
        self.assertIn("Drehzahl halten", w.band.text())
        ser.sim_taste("t3_lang")                            # Druck beim Messen: übernimmt nicht, Zange bekommt Antwort
        self.assertTrue(self.run_gui(3, lambda: ser.z_led == [tasten.LED_LAEUFT] * 2))
        self.assertTrue(self.run_gui(4, lambda: w.ratio_mess.zustand == w.ratio_mess.WARTET))
        self.assertTrue(self.run_gui(3, lambda: ser.z_led[-1] == tasten.LED_FERTIG))
        self.run_gui(0.2)
        self.assertIn("neu 6.8", w.band.text())
        self.assertIn("bisher 6.500", w.band.text())
        self.assertIn("T3 lang noch einmal", w.band.text())
        self.shot("uebersetzung.png")
        self.assertEqual(ratio.value(), 6.5)                # ohne Bestätigung ändert sich nichts
        neu = w.ratio_mess.wert
        ser.sim_taste("t3_lang")
        self.assertTrue(self.run_gui(3, lambda: ser.z_led[-1] == tasten.LED_OK))
        self.assertEqual(ratio.value(), neu)
        self.assertEqual(w.settings["params"]["ratio"], neu)
        self.assertIn("übernommen", w.band.text())
        self.assertFalse(w.ratio_mess.aktiv)

    def test_uebersetzung_verfaellt(self):
        w = self.w
        ser = self.verbinden()
        self.hold(ser)
        w.ratio_mess.mess_s = 1.0
        w.ratio_mess.bestaetigen_s = 0.5
        ratio = w.page_settings.fields["ratio"]
        ratio.setValue(6.5)
        ser.sim_taste("t3_lang")
        self.assertTrue(self.run_gui(4, lambda: ser.z_led[-1:] == [tasten.LED_FEHLER]))
        self.assertEqual(ratio.value(), 6.5)
        self.assertIn("verworfen", w.band.text())

    def test_uebersetzung_doppelt_zaehlend(self):
        w = self.w
        ser = self.verbinden()
        self.hold(ser)
        w.ratio_mess.mess_s = 1.0
        ratio = w.page_settings.fields["ratio"]
        ratio.setValue(3.425)                               # Sim misst 6,85 = das Doppelte
        ser.sim_taste("t3_lang")
        self.assertTrue(self.run_gui(4, lambda: ser.z_led[-1:] == [tasten.LED_FEHLER]))
        self.assertIn("doppelt", w.band.text())
        self.assertFalse(w.ratio_mess.aktiv)
        self.assertEqual(ratio.value(), 3.425)

    def test_uebersetzung_nur_in_ruhe(self):
        w = self.w
        ser = self.verbinden()
        w.on_start()
        ser.sim_taste("t3_lang")
        self.assertTrue(self.run_gui(3, lambda: ser.z_led == [tasten.LED_FEHLER]))
        self.assertFalse(w.ratio_mess.aktiv)
        w.on_abort()
        self.run_gui(0.2)
        # Lauf per F1 beendet eine wartende Messung
        self.hold(ser)
        w.ratio_mess.mess_s = 1.0
        ser.z_led.clear()
        ser.sim_taste("t3_lang")
        self.assertTrue(self.run_gui(4, lambda: w.ratio_mess.zustand == w.ratio_mess.WARTET))
        w.on_start()
        self.assertTrue(self.run_gui(3, lambda: not w.ratio_mess.aktiv and ser.z_led[-1] == tasten.LED_FEHLER))
        self.assertIn("verworfen", w.band.text())
        w.on_abort()


if __name__ == "__main__":
    unittest.main()
