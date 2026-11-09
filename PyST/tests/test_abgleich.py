"""Fenster „Übersetzung live abgleichen“: Schritte mit Rundung und Grenzen, Tasten, Anzeige Rolle × Übersetzung,
Abbrechen/Übernehmen, ohne Rollensignal, Groesse; im Hauptfenster aus Popup und Einstellungen, mit dem Simulator."""
import gc
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtWidgets  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from pyst import abgleich_dialog as ad, physics, tasten, theme  # noqa: E402
from pyst.kacheln import zahl  # noqa: E402
from pyst.runner import IDLE, RUN, Live  # noqa: E402

APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def pump(sec: float = 0.0):
    end = time.time() + sec
    while True:
        APP.processEvents()
        if time.time() >= end:
            return
        time.sleep(0.01)


class FakeLink:
    def __init__(self):
        self.received = 0
        self.gestartet = 0

    def streaming(self) -> bool:
        return self.gestartet > 0

    def start(self):
        self.gestartet += 1


class FakeCtrl:
    def __init__(self, n_roll=437.5, n_meas=3010.0):
        self.link = FakeLink()
        self.state = IDLE
        self.params = physics.DynoParams()
        self.live = Live(n_roll=n_roll, n_meas=n_meas, v_kmh=n_roll / 60.0 * 1.5 * 3.6)

    def neu(self, n_roll):
        """Neues Telegramm"""
        self.live.n_roll = n_roll
        self.link.received += 1


class FakeSeite:
    """Was der Dialog von der Rolle/Lauf-Seite braucht: Feld „ratio“ (wie gui.PARAM_FIELDS) und die Art."""
    def __init__(self, wert=6.85, art="funken"):
        from pyst.gui import UEBERSETZUNG_ARTEN
        sp = QtWidgets.QDoubleSpinBox()
        sp.setRange(0.01, 100)
        sp.setDecimals(3)
        sp.setValue(wert)
        self.fields = {"ratio": sp}
        self.ratio_art = QtWidgets.QComboBox()
        for a, text in UEBERSETZUNG_ARTEN:
            self.ratio_art.addItem(text, a)
        self.ratio_art.setCurrentIndex(self.ratio_art.findData(art))


# ============================================================================================== Rechnen
class Rechnen(unittest.TestCase):
    def test_schritte_ohne_gleitkommareste(self):
        self.assertEqual(ad.schritt_wert(6.85, 0.05), 6.9)
        self.assertEqual(f"{ad.schritt_wert(6.85, 0.05):.3f}", "6.900")
        self.assertEqual(ad.schritt_wert(6.85, -0.05), 6.8)
        self.assertEqual(ad.schritt_wert(6.85, 0.1), 6.95)
        self.assertEqual(ad.schritt_wert(6.85, -1.0), 5.85)
        v = 6.85
        for _ in range(20):
            v = ad.schritt_wert(v, 0.05)
        self.assertEqual(v, 7.85)                          # 20 Schritte, kein Rest
        for _ in range(30):
            v = ad.schritt_wert(v, -0.1)
        self.assertEqual(v, 4.85)
        self.assertEqual(ad.schritt_wert(6.8499999, 0.05), 6.9)

    def test_grenzen(self):
        self.assertEqual(ad.schritt_wert(0.52, -0.05), 0.5)  # bis an die Grenze
        self.assertEqual(ad.schritt_wert(0.5, -0.05), 0.5)   # nicht darunter
        self.assertEqual(ad.schritt_wert(0.5, -1.0), 0.5)
        self.assertEqual(ad.schritt_wert(0.3, -0.05), 0.3)   # jenseits: in diese Richtung nicht weiter
        self.assertEqual(ad.schritt_wert(0.3, 0.05), 0.35)   # zurueck geht
        self.assertEqual(ad.schritt_wert(49.95, 1.0), 50.0)
        self.assertEqual(ad.schritt_wert(50.0, 0.05), 50.0)
        self.assertEqual(ad.schritt_wert(60.0, 0.1), 60.0)
        self.assertEqual(ad.schritt_wert(50.0, -0.1), 49.9)

    def test_tasten(self):
        C, K = QtCore.Qt, QtCore.Qt
        self.assertEqual(ad.tasten_schritt(K.Key_Up, C.NoModifier), 0.05)
        self.assertEqual(ad.tasten_schritt(K.Key_Down, C.NoModifier), -0.05)
        self.assertEqual(ad.tasten_schritt(K.Key_PageUp, C.NoModifier), 0.1)
        self.assertEqual(ad.tasten_schritt(K.Key_PageDown, C.NoModifier), -0.1)
        self.assertEqual(ad.tasten_schritt(K.Key_Up, C.ControlModifier), 1.0)
        self.assertEqual(ad.tasten_schritt(K.Key_Down, C.ControlModifier), -1.0)
        self.assertEqual(ad.tasten_schritt(K.Key_A, C.NoModifier), 0.0)

    def test_zangen_kennung(self):
        self.assertEqual(tasten.abgleich_kennung("T4"), "T4")
        self.assertEqual(tasten.abgleich_kennung("T1/Z2"), "T1")
        self.assertEqual(tasten.abgleich_kennung("t3"), "T3")
        self.assertIsNone(tasten.abgleich_kennung("Marker"))
        self.assertIsNone(tasten.abgleich_kennung(""))
        self.assertEqual(tasten.packen(tasten.ABGLEICH_SETUP), 0x111111)      # Marker auf allen sechs Gesten
        self.assertEqual(tasten.ABGLEICH_SCHRITTE, {"T1": -0.1, "T2": 0.1, "T3": -0.05, "T4": 0.05})

    def test_skala(self):
        from pyst.messen_page import DREHZAHL_STANDARD
        hi, zonen = ad.skala_bereich(None, DREHZAHL_STANDARD["zonen"])
        self.assertEqual(hi, 14000.0)
        self.assertEqual(zonen[-1], (11500.0, 14000.0, "err"))   # roter Bereich bis zum Skalenende
        self.assertEqual(zonen[0], (10500.0, 11500.0, "warn"))
        hi, zonen = ad.skala_bereich(9000.0, [(7500, 8500, "warn"), (8500, 9000, "err")])
        self.assertEqual((hi, zonen[-1]), (9000.0, (8500.0, 9000.0, "err")))
        m = ad.Drehzahlmesser(14000.0, zonen)
        self.assertEqual(m.winkel(0), 225.0)
        self.assertEqual(m.winkel(14000), -45.0)
        self.assertEqual(m.winkel(7000), 90.0)                   # Mitte oben
        self.assertEqual(m.winkel(20000), -45.0)                 # Anschlag
        self.assertEqual(m.teilung(), (1000.0, 500.0))


# ============================================================================================== Dialog
class Dialog(unittest.TestCase):
    def setUp(self):
        self.seite = FakeSeite()
        self.ctrl = FakeCtrl()
        self.c = self.ctrl
        self.dlg = ad.AbgleichDialog(None, self.seite, lambda: self.c)
        self.dlg.show()
        pump(0.05)

    def tearDown(self):
        self.dlg.done(0)
        self.dlg.deleteLater()
        theme.manager().setze(theme.TAG, speichern=False)
        gc.collect()
        APP.processEvents()

    def test_anzeige_rolle_mal_uebersetzung(self):
        d = self.dlg
        self.assertFalse(d.isModal())
        self.assertEqual(self.ctrl.link.gestartet, 1)            # Datenstrom an
        d.takt()
        self.assertAlmostEqual(d.n_motor, 437.5 * 6.85)
        self.assertEqual(d.zahl.text(), zahl(437.5 * 6.85))
        self.assertAlmostEqual(d.messer.wert, 437.5 * 6.85)
        self.assertIn("438 1/min", d.rolle.text())
        self.assertIn("km/h", d.rolle.text())
        self.assertEqual(d.zuendung.text(), "Zündung  3.010 1/min")
        self.assertEqual(d.hinweis.text(), "")
        self.ctrl.neu(500.0)
        d.knoepfe[0.05].click()
        self.assertEqual(self.seite.fields["ratio"].value(), 6.9)  # gleiche Einstellung, sofort
        self.assertEqual(self.ctrl.params.ratio, 6.9)               # Kachel rechnet mit
        self.assertEqual(d.zahl.text(), zahl(500.0 * 6.9))
        self.assertEqual(d.spin.text().replace(",", "."), "6.900")
        # Aenderung von aussen (Popup/Einstellungen) erscheint hier
        self.seite.fields["ratio"].setValue(7.25)
        self.assertEqual(d.wert(), 7.25)
        self.assertEqual(d.zahl.text(), zahl(500.0 * 7.25))

    def test_knoepfe_und_grenzen(self):
        d = self.dlg
        f = self.seite.fields["ratio"]
        for schritt, soll in ((1.0, 7.85), (0.1, 7.95), (0.05, 8.0), (-0.05, 7.95), (-0.1, 7.85), (-1.0, 6.85)):
            d.knoepfe[schritt].click()
            self.assertEqual(f.value(), soll, schritt)
        for b in d.knoepfe.values():
            self.assertTrue(b.autoRepeat())
            self.assertGreaterEqual(b.minimumHeight(), 44)
            self.assertGreaterEqual(b.height(), 44)
            self.assertGreaterEqual(b.width(), 44)
        f.setValue(0.6)
        d.knoepfe[-1.0].click()
        self.assertEqual(f.value(), 0.5)
        d.knoepfe[-0.05].click()
        self.assertEqual(f.value(), 0.5)
        f.setValue(49.5)
        d.knoepfe[1.0].click()
        d.knoepfe[1.0].click()
        self.assertEqual(f.value(), 50.0)

    def test_tastenkuerzel(self):
        d = self.dlg
        f = self.seite.fields["ratio"]
        QTest.keyClick(d.spin, QtCore.Qt.Key_Up)
        self.assertEqual(f.value(), 6.9)
        QTest.keyClick(d.spin, QtCore.Qt.Key_Down)
        QTest.keyClick(d.spin, QtCore.Qt.Key_Down)
        self.assertEqual(f.value(), 6.8)
        QTest.keyClick(d.spin, QtCore.Qt.Key_PageUp)
        self.assertEqual(f.value(), 6.9)
        QTest.keyClick(d.spin, QtCore.Qt.Key_PageDown)
        self.assertEqual(f.value(), 6.8)
        QTest.keyClick(d.spin, QtCore.Qt.Key_Up, QtCore.Qt.ControlModifier)
        self.assertEqual(f.value(), 7.8)
        QTest.keyClick(d.spin, QtCore.Qt.Key_Down, QtCore.Qt.ControlModifier)
        self.assertEqual(f.value(), 6.8)
        QTest.keyClick(d, QtCore.Qt.Key_Up)                      # auch ohne Fokus im Feld
        self.assertEqual(f.value(), 6.85)
        self.assertIn("Strg", d.tasten.text())
        # direkt eintragen
        d.spin.lineEdit().selectAll()
        QTest.keyClicks(d.spin.lineEdit(), "7,123" if d.spin.locale().decimalPoint() == "," else "7.123")
        QTest.keyClick(d.spin, QtCore.Qt.Key_Return)
        self.assertEqual(f.value(), 7.123)
        self.assertTrue(d.isVisible())                           # Enter schliesst nicht

    def test_abbrechen_stellt_zurueck(self):
        d = self.dlg
        f = self.seite.fields["ratio"]
        d.knoepfe[1.0].click()
        d.knoepfe[0.1].click()
        self.assertEqual(f.value(), 7.95)
        gemeldet = []
        d.uebernommen.connect(gemeldet.append)
        d.b_abbrechen.click()
        self.assertEqual(f.value(), 6.85)
        self.assertEqual(self.ctrl.params.ratio, 6.85)
        self.assertEqual(gemeldet, [])
        self.assertEqual(self.seite.ratio_art.currentData(), "funken")   # Art bleibt
        self.assertFalse(d.isVisible())

    def test_esc_wie_abbrechen(self):
        d = self.dlg
        d.knoepfe[0.05].click()
        QTest.keyClick(d, QtCore.Qt.Key_Escape)
        self.assertEqual(self.seite.fields["ratio"].value(), 6.85)

    def test_uebernehmen_speichert(self):
        d = self.dlg
        gemeldet = []
        d.uebernommen.connect(gemeldet.append)
        d.knoepfe[-0.1].click()
        d.b_ok.click()
        self.assertEqual(gemeldet, [6.75])
        self.assertEqual(self.seite.fields["ratio"].value(), 6.75)
        self.assertEqual(self.seite.ratio_art.currentData(), "hand")
        self.assertFalse(d.isVisible())
        self.seite.fields["ratio"].setValue(5.0)                    # danach nicht mehr verbunden
        self.assertEqual(d.wert(), 6.75)

    def test_ohne_rollensignal(self):
        d = self.dlg
        self.c = None                                               # nicht verbunden
        d.takt()
        self.assertEqual(d.zahl.text(), "–")
        self.assertIsNone(d.messer.wert)
        self.assertEqual(d.hinweis.text(), "Keine Rollendrehzahl – Messboard verbinden")
        self.assertEqual(d.rolle.text(), "Rolle  –")
        d.knoepfe[0.05].click()                                     # verstellen geht trotzdem
        self.assertEqual(self.seite.fields["ratio"].value(), 6.9)
        self.c = self.ctrl                                          # verbunden, Rolle steht
        self.ctrl.neu(0.0)
        d.takt()
        self.assertEqual(d.zahl.text(), "–")
        self.assertIn("Keine Rollendrehzahl", d.hinweis.text())
        self.ctrl.neu(400.0)
        d.takt()
        self.assertEqual(d.zahl.text(), zahl(400.0 * 6.9))
        d._empfangen = (self.ctrl.link.received, time.time() - 2 * ad.VERALTET_S)   # keine Telegramme mehr
        d.takt()
        self.assertEqual(d.zahl.text(), "–")

    def test_im_lauf_gesperrt(self):
        d = self.dlg
        self.ctrl.state = RUN
        d.takt()
        self.assertFalse(d.knoepfe[0.05].isEnabled())
        d.schritt(0.05)
        self.assertEqual(self.seite.fields["ratio"].value(), 6.85)
        self.assertIn("Lauf", d.hinweis.text())

    def test_zangen_tasten(self):
        d = self.dlg
        f = self.seite.fields["ratio"]
        self.assertFalse(d.zange_zeile.isVisibleTo(d))              # ohne Zange keine Zeile
        for k, soll in (("T4", 6.9), ("T4", 6.95), ("T2", 7.05), ("T1", 6.95), ("T3", 6.9)):
            self.assertTrue(d.zange_taste(k))
            self.assertEqual(f.value(), soll, k)
        self.assertEqual(d.zange_meldung.text(), "Zange T3: −0,05")
        self.assertEqual(d.spin.property("hervor"), True)            # Wert springt sichtbar
        d._blink_bis = time.time() - 1
        d.takt()
        self.assertEqual(d.spin.property("hervor"), False)
        self.assertFalse(d.zange_taste("T9"))
        f.setValue(0.55)
        self.assertTrue(d.zange_taste("T3"))
        self.assertEqual(f.value(), 0.5)
        self.assertFalse(d.zange_taste("T1"))                      # Grenze
        self.assertEqual(f.value(), 0.5)
        self.assertIn("Grenze", d.zange_meldung.text())
        f.setValue(49.95)
        d.zange_taste("T2")
        self.assertFalse(d.zange_taste("T4"))
        self.assertEqual(f.value(), 50.0)
        self.ctrl.state = RUN                                        # im Lauf gesperrt
        d.takt()
        self.assertFalse(d.zange_taste("T3"))
        self.assertEqual(f.value(), 50.0)
        self.assertIn("gesperrt", d.zange_meldung.text())

    def test_zangen_zeile(self):
        text = ["Zange nicht verbunden"]
        d = ad.AbgleichDialog(None, self.seite, lambda: self.c, zange=lambda: text[0])
        try:
            d.show()
            pump(0.02)
            self.assertEqual(d.zange_zeile.text(), "Zange nicht verbunden")
            text[0] = tasten.ABGLEICH_TEXT
            d.takt()
            self.assertEqual(d.zange_zeile.text(), "Zange: T1 −0,1 · T2 +0,1 · T3 −0,05 · T4 +0,05")
            self.assertTrue(d.zange_zeile.isVisible())
        finally:
            d.done(0)
            d.deleteLater()

    def test_passt_auf_1280x800_tag_und_nacht(self):
        d = self.dlg
        ms = d.minimumSizeHint()
        self.assertLessEqual(ms.width(), 1240)
        self.assertLessEqual(ms.height(), 720)
        self.assertLessEqual(d.width(), 1280)
        self.assertLessEqual(d.height(), 760)
        d.resize(ms)
        pump(0.05)
        self.assertGreaterEqual(d.messer.width(), 300)
        for th in (theme.TAG, theme.NACHT):
            theme.manager().setze(th, speichern=False)
            pump(0.02)
            self.assertIn(theme.tokens()["warn_fg"], d.hinweis.styleSheet())
            self.assertFalse(d.grab().isNull())


# ============================================================================================== Hauptfenster
class ImHauptfenster(unittest.TestCase):
    def setUp(self):
        from pyst import gui
        self.gui = gui
        self.w = gui.MainWindow()
        self.w.resize(1280, 760)
        self.w.show()
        pump(0.1)

    def tearDown(self):
        from conftest import close_window
        close_window(self.w, APP)
        theme.manager().setze(theme.TAG, speichern=False)
        gc.collect()
        APP.processEvents()

    def test_aus_popup_und_einstellungen(self):
        gui, w = self.gui, self.w
        s = w.page_settings
        s.fields["ratio"].setValue(6.85)
        s.ratio_art.setCurrentIndex(s.ratio_art.findData("funken"))
        w._lauf_popup_zeigen()
        p = w.lauf_popup
        p.b_abgleich.click()
        self.assertFalse(p.isVisible())
        d = w.abgleich_dlg
        self.assertIsInstance(d, ad.AbgleichDialog)
        self.assertTrue(d.isVisible())
        self.assertEqual(d.messer.hi, 14000.0)                     # Fahrzeug ohne eigene Drehzahl-Skala
        d.knoepfe[0.05].click()
        self.assertEqual(p.spins["ratio"].value(), 6.9)             # Popup-Feld zieht mit
        self.assertIn("6,900", w.lauf_btn.toolTip())
        s.abgleich_btn.click()                                      # schon offen: kein zweites Fenster
        self.assertIs(w.abgleich_dlg, d)
        d.b_ok.click()
        pump(0.05)
        self.assertIsNone(w.abgleich_dlg)
        self.assertEqual(s.ratio_art.currentData(), "hand")
        self.assertEqual(w.settings[gui.UEBERSETZUNG_ART], "hand")
        self.assertAlmostEqual(w.settings["params"]["ratio"], 6.9)
        self.assertEqual(gui.load_settings()["params"]["ratio"], 6.9)
        s.abgleich_btn.click()                                      # Einstellungen › Rolle/Lauf
        d = w.abgleich_dlg
        self.assertTrue(d.isVisible())
        d.knoepfe[1.0].click()
        d.reject()
        self.assertEqual(s.fields["ratio"].value(), 6.9)

    def test_mit_simulator(self):
        gui, w = self.gui, self.w
        w.page_settings.fields["ratio"].setValue(6.85)
        w.page_settings.port_combo.setEditText(gui.DEFAULT_SIM_PORT)
        self.assertTrue(w.on_connect())
        try:
            w.on_abgleich_live()
            d = w.abgleich_dlg
            ende = time.time() + 5.0
            while time.time() < ende and d.n_motor is None:
                pump(0.05)
            self.assertIsNotNone(d.n_motor)
            d.takt()
            lv = w.ctrl.live
            self.assertAlmostEqual(d.n_motor, lv.n_roll * 6.85, delta=1e-6)
            self.assertGreater(d.n_motor, 0.0)
            d.knoepfe[1.0].click()
            d.takt()
            self.assertAlmostEqual(d.n_motor, w.ctrl.live.n_roll * 7.85, delta=1e-6)
            self.assertEqual(w.ctrl.params.ratio, 7.85)
            d.b_abbrechen.click()
            self.assertEqual(w.ctrl.params.ratio, 6.85)
        finally:
            w.on_connect()


class MitZange(unittest.TestCase):
    """Hauptfenster gegen SIM:basis mit gekoppelter Zange: Tasten-Override, Tasten T1–T4, keine Marker."""

    def setUp(self):
        from pyst import gui
        from pyst.tasten import TastenSync
        self.gui, self.Sync = gui, TastenSync
        self.w = gui.MainWindow()
        self.w.resize(1280, 760)
        self.w.show()
        pump(0.1)

    def tearDown(self):
        from conftest import close_window
        close_window(self.w, APP)
        theme.manager().setze(theme.TAG, speichern=False)
        gc.collect()
        APP.processEvents()

    def warte(self, cond, sec=5.0):
        ende = time.time() + sec
        while time.time() < ende:
            APP.processEvents()
            if cond():
                return True
            time.sleep(0.01)
        return False

    def verbinden(self):
        w = self.w
        w.page_settings.port_combo.setEditText("SIM:basis")
        self.assertTrue(w.on_connect())
        ser = w.link.ser
        ser.settings["sim_zange_mark"] = "0"                    # keine zufälligen Tasten
        self.assertTrue(self.warte(lambda: w.page_tasten.sync.zustand == self.Sync.OK))
        return ser

    def test_override_tasten_und_keine_marker(self):
        w = self.w
        ser = self.verbinden()
        try:
            eigen = tasten.packen(w.page_tasten.setup)
            self.assertEqual(int(ser.z_tasten), eigen)
            s = w.page_settings
            s.fields["ratio"].setValue(6.85)
            w.on_abgleich_live()
            d = w.abgleich_dlg
            self.assertTrue(self.warte(lambda: ser.z_tasten == 0x111111))       # Override an der Zange
            self.assertEqual(w.settings.get("zange_tasten", {}).get("t3_kurz", "lauf"), "lauf")  # nicht gespeichert
            self.assertTrue(self.warte(lambda: d.zange_zeile.text() == tasten.ABGLEICH_TEXT))
            marker = len(w.page_oszi.markers)
            ereignisse = len(w.ctrl.basis_events)
            ser.z_led.clear()
            ser.sim_taste("t4_kurz")
            self.assertTrue(self.warte(lambda: s.fields["ratio"].value() == 6.9))
            self.assertTrue(self.warte(lambda: tasten.LED_OK in ser.z_led))
            self.assertEqual(d.zange_meldung.text(), "Zange T4: +0,05")
            ser.sim_taste("t4_lang")                                  # lang = dieselbe Kennung
            self.assertTrue(self.warte(lambda: s.fields["ratio"].value() == 6.95))
            ser.sim_taste("t2_kurz")
            self.assertTrue(self.warte(lambda: s.fields["ratio"].value() == 7.05))
            ser.sim_taste("t1_kurz")
            self.assertTrue(self.warte(lambda: s.fields["ratio"].value() == 6.95))
            ser.sim_taste("t3_lang")
            self.assertTrue(self.warte(lambda: s.fields["ratio"].value() == 6.9))
            ser._event("MARK", "zange", "T2/Z2")                    # zweite Zange
            self.assertTrue(self.warte(lambda: s.fields["ratio"].value() == 7.0))
            pump(0.3)
            self.assertEqual(len(w.page_oszi.markers), marker)          # keine Marker
            self.assertEqual(len(w.ctrl.basis_events), ereignisse)
            self.assertNotIn("Marker", w.statusBar().currentMessage())
            # Neustart der Zange bei offenem Fenster: Override wieder hinaus
            ser.sim_zange_neustart()
            self.assertTrue(self.warte(lambda: ser.z_tasten == 0x111111 and
                                       w.page_tasten.sync.zustand == self.Sync.OK))
            # Abbrechen: Wert zurück, eigenes Setup wieder an der Zange
            d.b_abbrechen.click()
            self.assertEqual(s.fields["ratio"].value(), 6.85)
            self.assertIsNone(w.page_tasten.override)
            self.assertTrue(self.warte(lambda: ser.z_tasten == eigen))
            # danach sind Zangen-Marker wieder Marker
            n = len(w.page_oszi.markers)
            ser.sim_taste("t1_kurz")
            self.assertTrue(self.warte(lambda: len(w.page_oszi.markers) == n + 1))
            self.assertEqual(s.fields["ratio"].value(), 6.85)
            # Übernehmen nimmt den Override ebenso zurück
            w.on_abgleich_live()
            self.assertTrue(self.warte(lambda: ser.z_tasten == 0x111111))
            ser.sim_taste("t2_kurz")
            self.assertTrue(self.warte(lambda: s.fields["ratio"].value() == 6.95))
            w.abgleich_dlg.b_ok.click()
            self.assertTrue(self.warte(lambda: ser.z_tasten == eigen))
            self.assertAlmostEqual(w.settings["params"]["ratio"], 6.95)
        finally:
            w.on_connect()

    def test_blitzmodus_und_ohne_zange(self):
        from pyst.basis import ZangeStatus
        w = self.w
        w.on_abgleich_live()
        d = w.abgleich_dlg
        d.takt()
        self.assertEqual(d.zange_zeile.text(), "Zange nicht verbunden")
        w.page_tasten.sync.zange(True)
        w.page_hw.zst = ZangeStatus(blitz=1)
        d.takt()
        self.assertEqual(d.zange_zeile.text(), "Zange im Blitzmodus – Tasten wirken dort nicht")
        from pyst.basis import parse_event
        self.assertFalse(w._abgleich_marker(parse_event("E;100;MARK;zange;T1")))   # fester Marker im Blitzmodus
        w.page_hw.zst = None
        self.assertTrue(w._abgleich_marker(parse_event("E;100;MARK;zange;T4")))
        self.assertFalse(w._abgleich_marker(parse_event("E;100;MARK;pyst;Marker")))
        self.assertEqual(w.page_settings.fields["ratio"].value(), round(d.start_wert + 0.05, 3))
        d.reject()
        self.assertFalse(w._abgleich_marker(parse_event("E;100;MARK;zange;T4")))   # Fenster zu: wieder Marker


if __name__ == "__main__":
    unittest.main()
