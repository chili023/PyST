"""Analogeingänge (analog.py, analog_page.py): Umrechnung, Speichern, Protokoll, Simulator, Lauf und Oberfläche.

Nur Simulator und Attrappen, kein serieller Port.

    python -m pytest tests/test_analog.py
"""
import importlib
import json
import math
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from conftest import close_window  # noqa: E402
from pyst import analog, channels, link, physics, storage  # noqa: E402
from pyst.sim import SimEngine  # noqa: E402

DRUCK = dict(eingang="AI1", aktiv=True, name="Öldruck", einheit="bar", art="2punkt", u1=0.5, w1=0.0, u2=4.5,
             w2=10.0, lo=0.0, hi=10.0, dez=2)


class Umrechnung(unittest.TestCase):
    def test_zweipunkt(self):
        k = analog.Kanal.aus_dict(DRUCK, "AI1")
        self.assertAlmostEqual(k.wert(0.5), 0.0)
        self.assertAlmostEqual(k.wert(2.5), 5.0)
        self.assertAlmostEqual(k.wert(4.5), 10.0)
        self.assertAlmostEqual(k.wert(0.0), -1.25)                # linear auch ausserhalb
        np.testing.assert_allclose(k.werte([0.5, 4.5, np.nan]), [0.0, 10.0, np.nan])
        self.assertEqual(k.titel(), "Öldruck")
        self.assertEqual(k.einheit_text(), "bar")
        self.assertIn("0,5 V = 0", k.umrechnung_text())
        self.assertEqual(k.bereich(10.0), (0.0, 10.0))
        self.assertEqual(k.fehler(), "")

    def test_faktor_offset_und_keine(self):
        k = analog.Kanal("A0", art=analog.FAKTOR, a=3.0, b=1.0, einheit="V")
        self.assertAlmostEqual(k.wert(2.0), 7.0)
        self.assertIn("W = 3 · U + 1", k.umrechnung_text())
        self.assertEqual(k.bereich(5.0), (1.0, 16.0))
        k = analog.Kanal("A0", art=analog.FAKTOR, a=2.0, b=-1.0)
        self.assertIn("− 1", k.umrechnung_text())
        k = analog.Kanal("A0")
        self.assertEqual(k.art, analog.KEINE)
        self.assertAlmostEqual(k.wert(1.234), 1.234)
        self.assertEqual(k.einheit_text(), "V")
        self.assertEqual(k.umrechnung_text(), "keine (V)")
        self.assertEqual(k.bereich(5.0), (0.0, 5.0))
        self.assertIsNone(k.wert(None))
        self.assertIsNone(k.wert(float("nan")))

    def test_gleiche_spannungen_abgefangen(self):
        k = analog.Kanal("AI1", art=analog.ZWEIPUNKT, u1=2.0, w1=0.0, u2=2.0, w2=10.0)
        self.assertIsNone(k.koeffizienten())
        self.assertIsNone(k.wert(2.0))
        self.assertTrue(np.all(np.isnan(k.werte([1.0, 2.0]))))
        self.assertIn("U1 und U2", k.fehler())
        lo, hi = k.bereich(10.0)                                  # Balken trotzdem brauchbar
        self.assertLess(lo, hi)

    def test_aus_dict_robust(self):
        k = analog.Kanal.aus_dict({"aktiv": 1, "name": None, "art": "quatsch", "u1": "x", "lo": "nan", "dez": 99},
                                  "AI2")
        self.assertTrue(k.aktiv)
        self.assertEqual(k.name, "")
        self.assertEqual(k.art, analog.KEINE)
        self.assertEqual(k.u1, 0.5)
        self.assertIsNone(k.lo)
        self.assertEqual(k.dez, 4)
        self.assertEqual(analog.Kanal.aus_dict("kaputt", "AI1"), analog.Kanal("AI1"))
        # Vorlage traegt Umrechnung, Einheit und Balken ein, Name und Haken bleiben
        k = analog.Kanal("AI1", aktiv=True, name="Öldruck")
        k.vorlage(dict(analog.VORLAGEN)["Drucksensor 0,5–4,5 V = 0–10 bar"])
        self.assertEqual((k.name, k.aktiv, k.einheit, k.art), ("Öldruck", True, "bar", analog.ZWEIPUNKT))
        self.assertAlmostEqual(k.wert(4.5), 10.0)

    def test_geraete_und_schluessel(self):
        self.assertEqual([e.id for e in analog.GERAETE[analog.BASIS].eingaenge], ["AI1", "AI2"])
        self.assertEqual([e.id for e in analog.GERAETE[analog.F446].eingaenge], ["ADC1", "ADC2"])
        self.assertEqual(len(analog.GERAETE[analog.MEGA].eingaenge), 16)
        ids = [e.id for g in analog.GERAETE.values() for e in g.eingaenge]
        self.assertEqual(len(ids), len(set(ids)))                 # Eingaenge ueber alle Geraete eindeutig
        self.assertIn("afr_src", analog.GERAETE[analog.F446].eingaenge[0].fest)
        self.assertEqual(analog.eingang("ADC2")[0], analog.F446)
        self.assertTrue(analog.ist_key("ain:A15"))
        self.assertFalse(analog.ist_key("ain:A16"))
        from pyst import db
        self.assertTrue(db.live_kanal_ok("ain:AI1"))
        self.assertFalse(db.live_kanal_ok("ain:XYZ"))


class Einstellungen(unittest.TestCase):
    def tearDown(self):
        analog.laden({})

    def test_speichern_und_laden(self):
        s = {}
        analog.laden(s)
        self.assertEqual(analog.aktive(), [])
        liste = analog.kanaele(analog.BASIS)
        liste[0] = analog.Kanal.aus_dict(DRUCK, "AI1")
        analog.speichern(s, analog.BASIS, liste)
        self.assertEqual(s["analog"]["basis"][0]["name"], "Öldruck")
        # Neustart: aus der Datei
        s2 = json.loads(json.dumps(s))
        analog.laden({})
        self.assertEqual(analog.aktive(), [])
        analog.laden(s2)
        (g, k), = analog.aktive()
        self.assertEqual((g, k.name, k.einheit), (analog.BASIS, "Öldruck", "bar"))
        self.assertEqual(analog.bereich("ain:AI1"), (0.0, 10.0, 2))
        self.assertEqual(analog.kanal("ain:AI1")[1].name, "Öldruck")
        self.assertIsNone(analog.kanal("egt1"))
        # andere Geraete bleiben unberuehrt, kaputte Eintraege werden Standard
        s2["analog"]["mega"] = [{"eingang": "A3", "aktiv": True}, "kaputt", {"eingang": 5}]
        analog.laden(s2)
        self.assertEqual([k.eingang for _g, k in analog.aktive(analog.MEGA)], ["A3"])
        self.assertEqual(len(analog.kanaele(analog.MEGA)), 16)
        analog.set_neu_rechnen(s2, True)
        self.assertTrue(analog.neu_rechnen() and s2["analog_neu_rechnen"])


class Protokoll(unittest.TestCase):
    def test_a_zeile(self):
        t, w = analog.parse_a_line("A;1234;AI1=1.250;AI2=nan;kaputt;X=abc")
        self.assertEqual(t, 1234.0)
        self.assertEqual(w["AI1"], 1.25)
        self.assertTrue(math.isnan(w["AI2"]) and math.isnan(w["X"]))
        self.assertNotIn("kaputt", w)
        self.assertIsNone(analog.parse_a_line("A;x;AI1=1"))
        self.assertEqual(link.parse_line("A;5;ADC1=0.5", 0.0)[0], "A")

    def test_stat_der_basis(self):
        lines = ["#STAT;egt1=nan;egt1_st=offen", "#STAT;u12=12.16;u5s=4.963;ai1=0.011V;ai2=4.02mA;vref=2.497;t_mcu=30.9"]
        w, e = analog.parse_stat(lines)
        self.assertEqual(w, {"AI1": 0.011, "AI2": 4.02})
        self.assertEqual(e, {"AI1": "V", "AI2": "mA"})
        self.assertEqual(analog.parse_stat(["#OK;STAT"]), ({}, {}))

    def test_caps(self):
        self.assertEqual(analog.caps_eingaenge({"ai": "AI1, AI2"}), ["AI1", "AI2"])
        self.assertEqual(analog.caps_eingaenge({}), [])

    def test_geraet_von_link(self):
        class L:
            is_basis, is_zange_usb, is_stm = False, False, True
        self.assertEqual(analog.geraet_von_link(L()), analog.F446)
        L.is_stm = False
        self.assertEqual(analog.geraet_von_link(L()), analog.MEGA)
        L.is_basis = True
        self.assertEqual(analog.geraet_von_link(L()), analog.BASIS)
        L.is_zange_usb = True
        self.assertIsNone(analog.geraet_von_link(L()))
        self.assertIsNone(analog.geraet_von_link(None))


class Simulator(unittest.TestCase):
    def test_mega_a_zeilen_an_den_telegrammen(self):
        L = link.DynoLink("SIM:mega")
        try:
            time.sleep(0.2)
            L.info()
            caps = L.probe_caps()
            self.assertEqual(analog.caps_eingaenge(caps)[:2], ["A0", "A1"])
            self.assertTrue(L.analog_ein(10))
            L.start()
            time.sleep(0.8)
            fr = L.drain_frames()
            self.assertGreater(len(fr), 10)
            mit = [f for f in fr if f.ain]
            self.assertTrue(mit)
            self.assertEqual(len(mit[-1].ain), 16)
            self.assertTrue(0.0 <= mit[-1].ain["A0"] <= 5.0)
            self.assertEqual(L.analog_frisch(), L.analog)
        finally:
            L.close()

    def test_alte_firmware_ohne_ai(self):
        L = link.DynoLink("SIM:mega300")
        try:
            time.sleep(0.2)
            L.probe_caps()
            self.assertFalse(L.analog_ein(10))
            self.assertEqual(L.analog, {})
        finally:
            L.close()

    def test_basis_stat(self):
        L = link.DynoLink("SIM:basis")
        try:
            time.sleep(0.2)
            L.probe_caps()
            self.assertTrue(L.is_basis)
            self.assertFalse(L.analog_ein(10))                     # Basis 0.4 kennt '#AI' nicht
            w = L.analog_stat()
            self.assertEqual(set(w), {"AI1", "AI2"})
            self.assertTrue(0.4 < w["AI1"] < 4.6)
            self.assertEqual(L.analog_einheit["AI1"], "V")
        finally:
            L.close()


def _lauf_frames(ain_von=lambda i: {"AI1": 0.5 + i * 0.001, "AI2": 2.0}):
    """Ein synthetischer Leistungslauf (ohne Echtzeit) mit Analogwerten an jedem Telegramm."""
    eng = SimEngine()
    eng.reset()
    frames = []
    t = 0.0
    for i in range(int(60 * 30)):
        dt, rate, f_ign, f_roll, e1, afr, e2 = eng.next_frame()
        t += dt
        frames.append(link.Frame(t, i + 1, rate, f_ign, f_roll, e1, afr, e2, ain=ain_von(i)))
        if eng.phase == "coast" and eng.t_phase > 1.0:
            break
    return frames


class _Ctrl:
    def __init__(self, frames, basis=True):
        class FakeLink:
            port, lost = "SIM:basis", 0
            is_basis, is_zange_usb, is_stm = basis, False, True
            analog_einheit = {"AI1": "V", "AI2": "V"}
        self.link, self.frames, self.climate = FakeLink(), frames, None
        self.params = physics.DynoParams(n_stop=0)
        n, dt, nm, afr, egt = self.arrays()
        self.result = physics.evaluate(n, dt, self.params, nm)

    def arrays(self):
        p, fr = self.params, self.frames
        return (np.array([f.roll_hz for f in fr]) * 60.0 / p.inkr, np.array([1.0 / f.rate for f in fr]),
                np.array([f.ign_hz for f in fr]) * 60.0 / p.imp, np.zeros(len(fr)), np.zeros(len(fr)))


class Lauf(unittest.TestCase):
    def setUp(self):
        s = {}
        analog.laden(s)
        liste = analog.kanaele(analog.BASIS)
        liste[0] = analog.Kanal.aus_dict(DRUCK, "AI1")
        analog.speichern(s, analog.BASIS, liste)
        self.s = s

    def tearDown(self):
        analog.laden({})

    def test_roh_csv_lauf_json_und_kanaele(self):
        c = _Ctrl(_lauf_frames())
        with tempfile.TemporaryDirectory() as d:
            folder = storage.save_run(c, base_dir=d)
            with open(os.path.join(folder, "roh.csv"), encoding="utf-8") as fh:
                kopf = fh.readline().strip().split(";")
            self.assertEqual(kopf[-2:], ["analog_AI1", "analog_AI2"])    # Rohspannungen bleiben
            with open(os.path.join(folder, "lauf.json"), encoding="utf-8") as fh:
                meta = json.load(fh)
            a = meta["analog"]
            self.assertEqual((a["geraet"], a["eingaenge"], a["einheiten"]["AI1"]), ("basis", ["AI1", "AI2"], "V"))
            self.assertEqual(a["kanaele"][0]["name"], "Öldruck")
            self.assertEqual(a["kanaele"][0]["u2"], 4.5)
            run = storage.load_any(folder)
        ch = channels.run_channels(run, storage.recompute(run), run["params"])
        k = ch["Öldruck (Analog)"]
        self.assertEqual((k.unit, k.group), ("bar", "Analog"))
        self.assertAlmostEqual(k.values[0], (0.5 + 0.0 - 0.5) * 2.5, places=4)
        self.assertAlmostEqual(k.values[400], (0.5 + 0.4 - 0.5) * 2.5, places=3)
        self.assertIn("AI1 Rohwert (Analog)", ch)
        self.assertIn("AI2 Rohwert (Analog)", ch)                       # inaktiv: nur der Rohwert
        self.assertNotIn("AI2 (Analog)", ch)
        self.assertNotIn("analog_AI1", ch)                               # keine doppelte Zusatzspalte
        # geaenderte Umrechnung: der Lauf bleibt bei seiner, mit „aktuelle Umrechnung“ gilt die neue
        liste = analog.kanaele(analog.BASIS)
        liste[0].w2 = 20.0
        analog.speichern(self.s, analog.BASIS, liste)
        ch = channels.run_channels(run, None, run["params"])
        self.assertAlmostEqual(ch["Öldruck (Analog)"].values[400], 1.0, places=3)
        analog.set_neu_rechnen(self.s, True)
        ch = channels.run_channels(run, None, run["params"])
        self.assertAlmostEqual(ch["Öldruck (Analog)"].values[400], 2.0, places=3)

    def test_lauf_ohne_analog_und_alter_lauf(self):
        c = _Ctrl(_lauf_frames(lambda i: None))
        with tempfile.TemporaryDirectory() as d:
            folder = storage.save_run(c, base_dir=d)
            with open(os.path.join(folder, "lauf.json"), encoding="utf-8") as fh:
                meta = json.load(fh)
            self.assertIsNone(meta["analog"])
            run = storage.load_any(folder)
        ch = channels.run_channels(run, None, run["params"])
        self.assertFalse([n for n in ch if n.endswith(analog.SUFFIX)])
        # alter Lauf (LabVIEW-XML aus den Testdaten) laedt weiter
        data = os.path.join(os.path.dirname(__file__), "data")
        xml = sorted(f for f in os.listdir(data) if f.endswith(".xml"))[0]
        run = storage.load_any(os.path.join(data, xml))
        self.assertEqual(analog.lauf_kanaele(run), [])
        ch = channels.run_channels(run, None, run["params"])
        self.assertIn("Drehzahl Rolle", ch)

    def test_lauf_mit_spalten_ohne_konfiguration(self):
        """roh.csv mit analog_-Spalten, lauf.json ohne Eintrag (von Hand gebaut): aktuelle Konfiguration."""
        run = {"columns": {"analog_AI1": np.array([0.5, 4.5])}, "meta": {}}
        (lk,) = analog.lauf_kanaele(run)
        self.assertFalse(lk.konfig_vom_lauf)
        np.testing.assert_allclose(lk.werte, [0.0, 10.0])


class Oberflaeche(unittest.TestCase):
    """Seite Einstellungen › Analogeingänge im Hauptfenster: Speichern, Neustart, Live-Kacheln, Diagramm."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = {k: os.environ.get(k) for k in ("HOME", "QT_QPA_PLATFORM")}
        os.environ["HOME"] = self.tmp.name
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6 import QtWidgets
        self.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        from pyst import flash, gui
        importlib.reload(gui)
        for name in ("list_serial_ports", "pst_board_ports", "zange_usb_ports"):
            p = mock.patch.object(gui, name, lambda: [])
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(flash, "list_ports", None)
        p.start()
        self.addCleanup(p.stop)
        self.gui = gui
        self.w = gui.MainWindow()

    def tearDown(self):
        if self.w is not None:
            close_window(self.w, self.app)
        self.w = None
        analog.laden({})
        self.tmp.cleanup()
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def pump(self, s):
        end = time.time() + s
        while time.time() < end:
            self.app.processEvents()
            time.sleep(0.01)

    def test_seite_speichert_und_neustart(self):
        from PySide6 import QtCore
        w = self.w
        pg = w.page_analog
        self.assertIs(w.seiten["analog"], pg)
        self.assertLessEqual(pg.minimumSizeHint().width(), 1240)
        pg.waehle_geraet(analog.BASIS)
        self.assertEqual(pg.tab.rowCount(), 2)
        self.assertEqual(pg.tab.item(0, 0).text(), "AI1 (X6.1)")
        pg.tab.item(0, 1).setCheckState(QtCore.Qt.Checked)
        pg.tab.item(0, 2).setText("Öldruck")
        pg.tab.setCurrentCell(0, 2)
        pg._vorlage(dict(analog.VORLAGEN)["Drucksensor 0,5–4,5 V = 0–10 bar"])
        self.assertEqual(pg.tab.item(0, 3).text(), "bar")
        self.assertIn("4,5 V = 10 bar", pg.tab.item(0, 4).text())
        # 2-Punkt mit U1 = U2: Hinweis, keine Umrechnung
        pg.f_u2.setValue(0.5)
        self.assertIn("U1 und U2", pg.fehler_label.text())
        self.assertTrue(pg.tab.item(0, 4).text().startswith("⚠"))
        pg.f_u2.setValue(4.5)
        self.assertEqual(pg.fehler_label.text(), "")
        # F446: fest belegte Eingaenge mit Hinweis
        pg.waehle_geraet(analog.F446)
        self.assertIn("afr_src = 1", pg.tab.item(0, 7).text())
        with open(self.gui.SETTINGS_FILE, encoding="utf-8") as fh:
            s = json.load(fh)
        self.assertEqual(s["analog"]["basis"][0]["name"], "Öldruck")
        self.assertTrue(s["analog"]["basis"][0]["aktiv"])
        # wirkt sofort: Live-Kanaele, Diagramm-Kandidaten
        kan = {k.schluessel: k for k in w._live_kanaele()}
        self.assertEqual((kan["ain:AI1"].titel, kan["ain:AI1"].einheit, kan["ain:AI1"].quelle),
                         ("Öldruck", "bar", "Analog"))
        self.assertIn(("x:ain:AI1", "Öldruck (Analog)", "bar"), w._lower_candidates())
        # Neustart
        close_window(self.w, self.app)
        analog.laden({})
        self.w = self.gui.MainWindow()
        self.assertEqual([k.name for _g, k in analog.aktive()], ["Öldruck"])
        self.w.page_analog.waehle_geraet(analog.BASIS)
        self.assertEqual(self.w.page_analog.tab.item(0, 2).text(), "Öldruck")

    def test_live_mit_simulierter_basis(self):
        from PySide6 import QtCore
        w = self.w
        pg = w.page_analog
        pg.waehle_geraet(analog.MEGA)
        pg.tab.item(0, 1).setCheckState(QtCore.Qt.Unchecked)
        pg.waehle_geraet(analog.BASIS)
        pg.tab.item(0, 1).setCheckState(QtCore.Qt.Checked)
        pg.tab.item(0, 2).setText("Öldruck")
        pg.tab.setCurrentCell(0, 2)
        pg._vorlage(dict(analog.VORLAGEN)["Drucksensor 0,5–4,5 V = 0–10 bar"])
        self.assertEqual(pg.tab.item(0, 5).text(), "–")             # nicht verbunden: kein Wert
        pg.waehle_geraet(analog.MEGA)                              # beim Verbinden: das verbundene Geraet
        w.page_settings.port_combo.setEditText("SIM:basis")
        w.on_connect()
        self.assertIsNotNone(w.link)
        w.live_platz_hinzufuegen("ain:AI1")
        k = w.live_kacheln[-1]
        ende = time.time() + 8
        while time.time() < ende and (pg.key != analog.BASIS or not w.link.analog or k.wert_text() in ("", "–")):
            self.pump(0.1)                                         # Basis-Kanal aktiv: '#STAT' auch ohne Seite
        self.assertEqual(pg.key, analog.BASIS)
        pg._live_zeigen()                                          # die Seite zeigt live nur, wenn sie sichtbar ist
        self.assertIn("V", pg.tab.item(0, 5).text())
        self.assertIn("bar", pg.tab.item(0, 6).text())
        self.assertIn("live", pg.chip.text())
        self.assertEqual(k.kanal, "ain:AI1")
        self.assertNotIn(k.wert_text(), ("", "–"))
        self.assertEqual(w._analog_live(link.Frame(0, 1, 60, 0, 0, ain={"AI1": 2.5}))["ain:AI1"], 5.0)

    def test_gespeicherter_lauf_im_diagramm_und_cursor(self):
        w = self.w
        s = w.settings
        liste = analog.kanaele(analog.BASIS)
        liste[0] = analog.Kanal.aus_dict(DRUCK, "AI1")
        analog.speichern(s, analog.BASIS, liste)
        c = _Ctrl(_lauf_frames())
        with tempfile.TemporaryDirectory() as d:
            folder = storage.save_run(c, base_dir=d)
            rid = w.db.import_file(folder)
        run, res, ch, _p = w._entry(rid)
        self.assertIn("Öldruck (Analog)", ch)
        extra = w._ecu_extra_run(run, res)
        self.assertIn("ain:AI1", extra)
        self.assertEqual(len(extra["ain:AI1"]), len(res.n))
        self.assertTrue(np.nanmax(extra["ain:AI1"]) > 0.5)
        w.plot.set_curves(str(rid), res.n, res.ps, res.nm, extra=extra)
        werte = w.lauf_werte_bei(str(rid), float(np.nanmedian(res.n)))
        self.assertIsNotNone(werte.get("ain:AI1"))
