"""Stufe 2 der neuen Oberflaeche, Reiter Auswertung: Laufleiste (Reihenfolge, Abweichungspfeile, Chips), Werte am
Cursor mehrerer Laeufe, Mittelwert-Dialog (Kanalzuordnung, Setup-Warnung, Speichern), Setup-Dialog eines Laufs und
das Zusammenspiel im LogViewer (mit synthetischen Laeufen, ohne Hauptfenster)."""
import os
import sys
import tempfile
import time
import unittest
from collections import OrderedDict
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtWidgets  # noqa: E402

from pyst import db as dbm  # noqa: E402
from pyst import mittelwert as mw  # noqa: E402
from pyst import theme  # noqa: E402
from pyst.channels import Channel  # noqa: E402
from pyst.cursorwerte import bereich_runden, dez_fuer, wert_bei  # noqa: E402
from pyst.kacheln import zahl  # noqa: E402
from pyst.lauf_dialog import LaufSetupDialog, geaenderte_felder, schnappschuss_nachfuehren  # noqa: E402
from pyst.laufleiste import (SCHWELLE_CHIPS, Zelle, chips_modus, datum_text, kurz_titel, reihenfolge,  # noqa: E402
                             schnellwert_zellen, zeit_text)
from pyst.mittelwert_dialog import (MittelwertDialog, abweichende, kanaele_aus_mittelwert,  # noqa: E402
                                    kurven_aus_kanaelen, mehrheit, namensvorschlag, setup_unterschiede,
                                    zusatz_lesen, zusatz_schreiben)
from pyst.viewer import LogViewer, lauf_bezeichnungen, laufwerte_am_cursor  # noqa: E402

APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def pump(sec: float = 0.0):
    end = time.time() + sec
    while True:
        APP.processEvents()
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.DeferredDelete)
        if time.time() >= end:
            return
        time.sleep(0.01)


def zeile(rid, datum, art=dbm.ART_LAUF, **kw):
    return SimpleNamespace(id=rid, datum=datum, art=art, ist_mittelwert=art == dbm.ART_MITTELWERT, **kw)


# ============================================================================================== reine Funktionen
class Laufleiste(unittest.TestCase):
    def test_datum_und_zeit(self):
        r = zeile(1, "2026-05-26T16:24:55")
        self.assertEqual(datum_text(r), "26.05.26 16:24")
        self.assertEqual(zeit_text(r), "16:24")

    def test_reihenfolge_mittelwert_oben_dann_alt_nach_neu(self):
        rows = [zeile(1, "2026-05-26T16:29:00"), zeile(2, "2026-11-08T10:00:00", dbm.ART_MITTELWERT),
                zeile(3, "2026-05-26T16:24:00"), zeile(4, "2026-05-26T16:28:00")]
        self.assertEqual([r.id for r in reihenfolge(rows)], [2, 3, 4, 1])

    def test_abweichungspfeile_wie_im_entwurf(self):
        felder = ["hauptduese", "clip", "zzp"]
        werte = [{"hauptduese": "135", "clip": "3", "zzp": 18.0},       # Ø-Lauf (Bezug)
                 {"hauptduese": "135", "clip": "3", "zzp": "18"},
                 {"hauptduese": "140", "clip": "3", "zzp": "18"},
                 {"hauptduese": "140", "clip": "2", "zzp": "18"}]
        z = schnellwert_zellen(werte, felder)
        self.assertEqual([c.text for c in z[0]], ["135", "3", "18"])
        self.assertFalse(any(c.geaendert for c in z[1]))               # 18.0 == "18"
        self.assertEqual(z[2][0].text, "140  ↑ 135")
        self.assertTrue(z[2][0].geaendert)
        self.assertEqual(z[3][1].text, "2  ↓ 3")
        self.assertEqual((z[3][0].text, z[3][0].geaendert), ("140", False))   # gleich wie die Zeile darueber

    def test_abweichung_text_leer_und_leerer_platz(self):
        z = schnellwert_zellen([{"auspuff": "Serie"}, {"auspuff": "Arrow"}, {"auspuff": ""}, {"auspuff": "Arrow"}],
                               ["auspuff", ""])
        self.assertEqual(z[1][0].text, "Arrow  → Serie")                # Text: Pfeil ohne Richtung
        self.assertEqual((z[2][0].text, z[2][0].geaendert), ("–", False))  # leer zaehlt nicht
        self.assertFalse(z[3][0].geaendert)
        self.assertEqual(z[0][1], Zelle(""))

    def test_chips_ab_schwelle(self):
        self.assertFalse(chips_modus(SCHWELLE_CHIPS - 1, False))
        self.assertTrue(chips_modus(SCHWELLE_CHIPS, False))
        self.assertFalse(chips_modus(SCHWELLE_CHIPS + 3, True))

    def test_kurze_spaltentitel(self):
        self.assertEqual(kurz_titel("clip"), "Clip")
        self.assertEqual(kurz_titel("zzp"), "ZZP [° v. OT]")
        self.assertEqual(kurz_titel("hauptduese"), "Hauptdüse")
        self.assertEqual(kurz_titel(""), "")


class Cursorwerte(unittest.TestCase):
    def test_wert_bei_naechster_punkt_und_ausserhalb(self):
        x = np.array([0.0, 1.0, 2.0, 3.0])
        y = np.array([10.0, 11.0, np.nan, 13.0])
        self.assertEqual(wert_bei(x, y, 1.2), 11.0)
        self.assertIsNone(wert_bei(x, y, 2.1))                         # naechster Punkt NaN -> –
        self.assertIsNone(wert_bei(x, y, 3.5))
        self.assertIsNone(wert_bei(x, y, -0.5))
        self.assertIsNone(wert_bei(None, y, 1.0))
        self.assertIsNone(wert_bei(x, y, None))

    def test_wert_bei_interpoliert(self):
        n = np.arange(5000.0, 9001.0, 50.0)
        ps = n / 1000.0
        self.assertAlmostEqual(wert_bei(n, ps, 6025.0, interpolieren=True), 6.025)
        self.assertIsNone(wert_bei(n, ps, 4900.0, interpolieren=True))

    def test_bereich_und_nachkommastellen(self):
        self.assertEqual(bereich_runden(0.3, 31.9), (0.0, 40.0))
        self.assertEqual(bereich_runden(6.62, 7.05), (6.6, 7.2))
        lo, hi = bereich_runden(5.0, 5.0)
        self.assertLess(lo, 5.0)
        self.assertGreater(hi, 5.0)
        self.assertEqual((dez_fuer("PS"), dez_fuer("λ"), dez_fuer("°C"), dez_fuer("nKW/nR")), (1, 2, 0, 3))
        self.assertEqual(dez_fuer("x", 5), 2)

    def test_lauf_bezeichnungen(self):
        rows = [zeile(9, "2026-11-08T10:00:00", dbm.ART_MITTELWERT), zeile(8, "2026-11-08T11:00:00", dbm.ART_MITTELWERT),
                zeile(1, "2026-05-26T16:24:00"), zeile(2, "2026-05-27T16:24:00"), zeile(3, "2026-05-26T16:28:00")]
        b = lauf_bezeichnungen(rows)
        self.assertEqual(b[9], "Ø Ref.")
        self.assertEqual(b[8], "Ø Ref. 2")
        self.assertEqual(b[1], "26.05. 16:24")                          # gleiche Uhrzeit an zwei Tagen
        self.assertEqual(b[3], "16:28")

    def test_laufwerte_mehrerer_laeufe(self):
        n1 = np.linspace(3000, 9000, 61)
        n_avg = np.arange(5000.0, 8001.0, 50.0)
        werte = laufwerte_am_cursor([("#1f77b4", "16:24", n1, n1 / 300.0, False),
                                     ("@avg_color", "Ø Ref.", n_avg, n_avg / 250.0, True),
                                     ("#d62728", "16:28", None, None, False)], 7010.0)
        self.assertEqual(werte[0][:2], ("#1f77b4", "16:24"))
        self.assertAlmostEqual(werte[0][2], 7000.0 / 300.0)
        self.assertAlmostEqual(werte[1][2], 7010.0 / 250.0)
        self.assertIsNone(werte[2][2])
        self.assertIsNone(laufwerte_am_cursor([("#1", "a", n1, n1, False)], None)[0][2])


class MittelwertLogik(unittest.TestCase):
    def kanaele(self, n, ps, egt=None):
        ch = OrderedDict()
        ch["Zeit"] = Channel(np.arange(len(n)) * 0.05, "s", "Zeit")
        ch["Drehzahl Motor"] = Channel(n, "1/min")
        ch["Leistung"] = Channel(ps, "PS", "Auswertung")
        ch["Übersetzung gefiltert"] = Channel(np.full(len(n), 6.8), "nKW/nR", "Übersetzung")
        ch["Übersetzung eingestellt"] = Channel(np.full(len(n), 6.8), "nKW/nR", "Übersetzung")
        ch["EGT 1"] = Channel(egt if egt is not None else np.full(len(n), 700.0), "°C", "Temperatur")
        ch["Fehler Rolle"] = Channel(np.zeros(len(n)), "0/1", "Fehler")
        ch["RPMValue (ECU)"] = Channel(n * 1.0, "rpm", "ECU")
        return ch

    def test_kurven_aus_kanaelen(self):
        n = np.linspace(2000, 9000, 50)
        egt = np.full(50, 700.0)
        egt[:3] = 0.0
        k, e = kurven_aus_kanaelen(self.kanaele(n, n / 300.0, egt), slice(0, 40))
        self.assertEqual(set(k), {"n", "ps", "egt1", "Übersetzung gefiltert", "RPMValue (ECU)"})
        self.assertEqual(len(k["n"]), 40)
        self.assertTrue(np.isnan(k["egt1"][:3]).all())
        self.assertEqual((e["ps"], e["Übersetzung gefiltert"]), ("PS", "nKW/nR"))
        with self.assertRaises(ValueError):
            kurven_aus_kanaelen({"Zeit": Channel(n, "s", "Zeit")})

    def test_rundreise_ueber_mittelwert(self):
        n = np.linspace(3000, 9000, 121)
        laeufe = [kurven_aus_kanaelen(self.kanaele(n, n / 300.0 + d))[0] for d in (0.0, 2.0)]
        res = mw.mitteln(laeufe)
        ch, baender = kanaele_aus_mittelwert(res, {"Übersetzung gefiltert": "nKW/nR"})
        self.assertIn("Leistung", ch)
        self.assertEqual(ch["Leistung"].unit, "PS")
        self.assertEqual(ch["Übersetzung gefiltert"].unit, "nKW/nR")
        self.assertNotIn("Zeit", ch)
        np.testing.assert_allclose(ch["Leistung"].values, res.n / 300.0 + 1.0)
        lo, hi = baender["Leistung"]["minmax"]
        np.testing.assert_allclose(hi - lo, 2.0)
        self.assertIn("sigma", baender["Leistung"])

    def test_ecu_schluessel_zurueck(self):
        res = mw.mitteln([{"n": np.linspace(3000, 9000, 20), "ecu:CLT": np.full(20, 80.0)},
                          {"n": np.linspace(3000, 9000, 20), "ecu:CLT": np.full(20, 90.0)}])
        ch, _b = kanaele_aus_mittelwert(res)
        self.assertIn("CLT (ECU)", ch)

    def test_setup_warnung_wie_im_entwurf(self):
        setups = [{"hauptduese": "135", "zzp": "17"}, {"hauptduese": "135", "zzp": "18"},
                  {"hauptduese": "135", "zzp": 18.0}]
        t = setup_unterschiede(setups, ["16:18", "16:19", "16:23"])
        self.assertEqual(t, "Die Setups der Quellläufe unterscheiden sich: Zündzeitpunkt 17 (16:18) / "
                            "18 (16:19, 16:23). Trotzdem mitteln?")
        self.assertEqual(setup_unterschiede(setups[1:], ["a", "b"]), "")
        self.assertEqual(abweichende(["17", "18", "18"]), [True, False, False])
        self.assertEqual(mehrheit(["17", "18", "18", ""]), "18")
        self.assertEqual(mehrheit(["", None]), "")

    def test_namensvorschlag_und_zusatz(self):
        self.assertEqual(namensvorschlag(3, "Serie, HD 135"), "Ø 3 Läufe · Serie HD 135")
        self.assertEqual(namensvorschlag(2), "Ø 2 Läufe")
        with tempfile.TemporaryDirectory() as d:
            res = mw.mitteln([{"n": np.linspace(3000, 9000, 20), "ps": np.ones(20)},
                              {"n": np.linspace(3000, 9000, 20), "ps": np.ones(20) * 3}])
            mw.schreiben(d, res, "x")
            self.assertTrue(zusatz_schreiben(d, band="sigma", einheiten={"ps": "PS"}))
            self.assertEqual(zusatz_lesen(d), {"band": "sigma", "einheiten": {"ps": "PS"}})
            self.assertEqual(mw.laden(d).anzahl, 2)                   # laden stoert sich nicht daran
        self.assertEqual(zusatz_lesen("/gibt/es/nicht"), {})


class SetupLogik(unittest.TestCase):
    def test_geaenderte_felder(self):
        alt = {"id": 3, "name": "Serie", "hauptduese": "135", "clip": 3.0, "zzp": "18"}
        neu = {"hauptduese": "140", "clip": "3", "zzp": "18", "nadel": ""}
        self.assertEqual(geaenderte_felder(alt, neu), {"hauptduese": "140"})

    def test_schnappschuss_nachfuehren(self):
        werte = [{"feld": "hauptduese", "wert": "135", "vorher": "130"}, {"feld": "clip", "wert": "3", "vorher": ""}]
        neu = schnappschuss_nachfuehren(werte, {"hauptduese": "140", "zzp": "17"})
        self.assertEqual(neu, [{"feld": "hauptduese", "wert": "140", "vorher": ""},
                               {"feld": "clip", "wert": "3", "vorher": ""}])
        self.assertEqual(werte[0]["wert"], "135")                       # Eingabe unveraendert


# ============================================================================================== Auswertung (Widget)
def synth_kanaele(rid: int):
    """Lauf mit steigender und fallender Drehzahl: Leistung = n/300 + rid/2, Kurvenbereich = steigender Teil."""
    up = np.linspace(3000, 9000, 121)
    n = np.concatenate([up, up[::-1][1:30]])
    ch = OrderedDict()
    ch["Zeit"] = Channel(np.arange(len(n)) * 0.05, "s", "Zeit")
    ch["Drehzahl Motor"] = Channel(n, "1/min")
    ch["Leistung"] = Channel(n / 300.0 + rid / 2.0, "PS", "Auswertung")
    ch["Drehmoment"] = Channel(np.full(len(n), 20.0 + rid), "Nm", "Auswertung")
    ch["Lambda"] = Channel(np.full(len(n), 0.9), "λ", "Gemisch")
    return ch, slice(0, 121), []


class Auswertung(unittest.TestCase):
    def setUp(self):
        theme.manager().setze(theme.TAG, speichern=False)
        self.tmp = tempfile.TemporaryDirectory()
        self.db = dbm.Database(os.path.join(self.tmp.name, "pyst.db"))
        self.vid = self.db.save_vehicle({"hersteller": "Aprilia", "modell": "RS 125"})
        basis = {"hauptduese": "135", "clip": "3", "zzp": "18", "vergaser": "PHBH 28"}
        self.s1 = self.db.save_setup(self.vid, "Serie, HD 135", basis)
        self.s2 = self.db.save_setup(self.vid, "HD 140", dict(basis, hauptduese="140"))
        self.ids = []
        for k, (sid, zeit) in enumerate([(self.s1, "16:18"), (self.s1, "16:19"), (self.s2, "16:24"), (self.s2, "16:28")]):
            res = SimpleNamespace(p_max=30.0 + k, n_pmax=9000.0, m_max=21.0 + k, n_mmax=8000.0, ka=0.0)
            rid = self.db.add_run(self.tmp.name, "", "PyST", f"Lauf {k}", f"2026-05-26T{zeit}:00", res,
                                  vehicle_id=self.vid, setup_id=sid,
                                  setup_werte=dbm.setup_schnappschuss(self.db.setup(sid), ["hauptduese", "clip", "zzp"]))
            self.ids.append(rid)
        self.geladen = []
        self.v = LogViewer(self.db, lambda rid: (self.geladen.append(rid), synth_kanaele(rid))[1])
        self.v.resize(1700, 1000)
        self.v.apply_layout({"felder": 2, "x": "Drehzahl Motor", "kanaele": [["Leistung", "Drehmoment"], ["Lambda"]]})
        self.v.show()
        pump(0.05)

    def tearDown(self):
        self.v.close()
        self.v.deleteLater()
        pump(0.02)
        self.db.con.close()
        self.tmp.cleanup()

    def test_kopfzeile_ohne_kachelzeile_und_volle_knoepfe(self):
        self.assertFalse(hasattr(self.v, "gauge_bar"))
        for b in (self.v.import_btn, self.v.pdf_btn, self.v.csv_knopf):
            self.assertGreaterEqual(b.width(), b.sizeHint().width() - 1, b.text())
        self.assertEqual(self.v.xaxis.currentText(), "Motordrehzahl")
        self.assertIs(self.v.split.widget(0), self.v.seitenleiste)

    def test_laufleiste_zeilen_und_pfeile(self):
        self.v.add_runs(self.ids[1:])
        pump()
        e = self.v.leiste.eintraege
        self.assertEqual([x.rid for x in e], self.ids[1:])               # alt -> neu
        self.assertEqual(self.v.leiste.felder, ["hauptduese", "clip", "zzp"])
        self.assertEqual(e[1].zellen[0].text, "140  ↑ 135")
        self.assertFalse(e[2].zellen[0].geaendert)
        self.assertIn("Aprilia RS 125", self.v.leiste.info)
        self.assertIn("31,0", e[0].pmax)
        self.assertEqual(self.v.leiste.zeilen[self.ids[1]]["knopf"].text(), "Setup …")
        self.assertIn("3 angezeigt", self.v.seitenleiste.streifen.text())

    def test_cursor_baut_keine_widgets_neu(self):
        """Cursorbewegung tauscht nur die Zahlen: gleiche Label-Objekte, kein neues Stylesheet (sonst ruckelt es und auf
        dem Mac bleiben Reste stehen); Diagramme zeichnen ganz neu."""
        self.v.add_runs(self.ids)
        pump(0.1)
        self.v._set_cursor_x(5000)
        self.v._update_cursor()
        k = next(k for k in self.v.cursor.kacheln if k.isVisible() and k.laeufe)
        vorher, texte = list(k._lauf_widgets), k.lauf_texte()
        css = [w.styleSheet() for w in vorher]
        self.v._set_cursor_x(8000)
        self.v._update_cursor()
        self.assertEqual([id(w) for w in k._lauf_widgets], [id(w) for w in vorher])
        self.assertNotEqual(k.lauf_texte(), texte)
        self.assertEqual([w.styleSheet() for w in k._lauf_widgets], css)
        self.assertEqual(self.v.glw.viewportUpdateMode(), QtWidgets.QGraphicsView.FullViewportUpdate)
        from pyst.plots import DynoPlot
        p = DynoPlot()
        self.assertEqual(p.viewportUpdateMode(), QtWidgets.QGraphicsView.FullViewportUpdate)
        p.deleteLater()

    def test_cursor_werte_sofort_wie_in_messen(self):
        """Jede Mausbewegung schreibt die Werte rechts sofort (kein Timer, der bei laufender Maus nie feuert) und tauscht
        nur die Zahlen; Bereiche und Kanalauswahl bleiben, bis sich Laeufe oder Felder aendern."""
        import time as _t
        v = self.v
        v.add_runs(self.ids[:2])
        pump(0.1)
        k = next(k for k in v.cursor.kacheln if k.isVisible() and k.laeufe)
        plan = v._cursor_plan
        self.assertIsNotNone(plan)
        dauer = []
        for n in range(4000, 8600, 150):                                  # Maus faehrt durch, ohne Ereignisschleife
            t0 = _t.perf_counter()
            v._set_cursor_x(float(n), frei=True)
            dauer.append(_t.perf_counter() - t0)
            self.assertEqual(v.cursor.x_kachel.wert.text(), zahl(float(n)))
            erwartet = [zahl(w, k.dez) for _f, _b, w in v._laufwerte(k.kanal, v._runs_shown(), v._primary(), n)]
            self.assertEqual([t for _b, t in k.lauf_texte()], erwartet)
        self.assertIs(v._cursor_plan, plan)                               # nichts neu aufgebaut
        self.assertLess(sorted(dauer)[len(dauer) // 2], 0.02)             # Median unter 20 ms
        v.leiste.zeilen[self.ids[1]]["anzeigen"].setChecked(False)        # anderer Lauf weg -> neu aufbauen
        pump()
        v._set_cursor_x(5000.0, frei=True)
        self.assertEqual(len(k.laeufe), 1)

    def test_cursor_folgt_maus_ueber_den_ersten_lauf_hinaus(self):
        """Zwei Laeufe, der zweite dreht hoeher: der Cursor geht bis ans Ende des zweiten; der erste zeigt dort „–“,
        unten an der Linie steht die genaue Drehzahl."""
        def laden(rid):
            ch, seg, mk = synth_kanaele(rid)
            if rid == self.ids[1]:
                for c in ch.values():
                    c.values = np.asarray(c.values, float).copy()
                ch["Drehzahl Motor"].values = ch["Drehzahl Motor"].values * 11000.0 / 9000.0
            return ch, seg, mk
        v = LogViewer(self.db, laden)
        v.resize(1700, 1000)
        v.apply_layout({"felder": 1, "x": "Drehzahl Motor", "kanaele": [["Leistung"]]})
        v.show()
        v.add_runs(self.ids[:2])
        pump(0.1)
        v._set_cursor_x(10500, frei=True)
        v._update_cursor()
        self.assertAlmostEqual(v.cursor_x(), 10500, delta=1)
        self.assertAlmostEqual(v.lines[0].value(), 10500, delta=1)
        self.assertEqual(v.cursor_fahne.textItem.toPlainText(), "10.500 1/min")
        k = next(k for k in v.cursor.kacheln if k.isVisible() and k.laeufe)
        werte = dict(k.lauf_texte())
        self.assertEqual(werte["16:18"], "–")                           # erster Lauf endet bei 9000
        self.assertNotEqual(werte["16:19"], "–")
        v._set_cursor_x(20000, frei=True)                               # rechts ausserhalb: am Ende des laengsten
        self.assertAlmostEqual(v.cursor_x(), 11000, delta=1)
        v.close()
        v.deleteLater()

    def test_notiz_sichtbar_entfernen(self):
        self.v.add_runs(self.ids[:3])
        pump()
        ed = self.v.leiste.zeilen[self.ids[0]]["notiz"]
        ed.setText("warm, 18 °C")
        ed.editingFinished.emit()
        self.assertEqual(self.db.run(self.ids[0]).notiz, "warm, 18 °C")
        self.v.leiste.zeilen[self.ids[1]]["anzeigen"].setChecked(False)
        pump()
        self.assertIn(self.ids[1], self.v.hidden)
        self.assertNotIn(self.ids[1], self.v._runs_shown())
        self.v.leiste.entfernt.emit(self.ids[2])
        pump()
        self.assertNotIn(self.ids[2], self.v.selected)

    def test_chips_ab_fuenf_laeufen(self):
        self.v.add_runs(self.ids)
        pump()
        self.assertFalse(self.v.leiste.ist_chips())
        res = mw.mitteln([{"n": np.linspace(3000, 9000, 50), "ps": np.ones(50)},
                          {"n": np.linspace(3000, 9000, 50), "ps": np.ones(50) * 2}], quellen=self.ids[:2])
        rid = mw.speichern(self.db, res, "Ø 2 Läufe")
        self.v.mittelwert_uebernehmen(rid)
        pump()
        self.assertTrue(self.v.leiste.ist_chips())
        self.assertTrue(self.v.leiste.klapp_btn.isVisibleTo(self.v))
        self.v.leiste.klapp_btn.click()
        pump()
        self.assertFalse(self.v.leiste.ist_chips())
        self.assertEqual(self.v.leiste.eintraege[0].rid, rid)           # Mittelwert oben

    def test_werte_am_cursor_mehrerer_laeufe(self):
        self.v.add_runs(self.ids[:3])
        pump()
        self.v._set_cursor_x(6000)
        self.v._update_cursor()
        x_at = self.v.cursor_x()
        self.assertAlmostEqual(x_at, 6000.0, delta=30)
        kach = [k for k in self.v.cursor.kacheln if k.isVisibleTo(self.v)]
        self.assertEqual([k.kanal for k in kach], ["Leistung", "Drehmoment", "Lambda"])
        texte = kach[0].lauf_texte()
        self.assertEqual([t for t, _w in texte], ["16:18", "16:19", "16:24"])
        for (t, w), rid in zip(texte, self.ids[:3]):
            self.assertEqual(w, f"{x_at / 300.0 + rid / 2.0:.1f}".replace(".", ","))
        self.assertEqual(len(kach[0].balken.marken), 3)
        self.assertEqual(self.v.cursor.x_kachel.titel.text(), "Motordrehzahl am Cursor")
        self.assertEqual([t for t, _w in self.v.cursor.pmax.lauf_texte()], ["16:18", "16:19", "16:24"])
        self.assertEqual(self.v.cursor.pmax.lauf_texte()[2][1], "32,0")
        self.assertEqual(self.v.cursor.pmax.quelle.text(), "PS")
        # Kanal einer Kachel per Titel ▾ waehlen
        kach[2].waehle("Drehmoment")
        self.assertEqual(self.v.kachel_wahl, {"Lambda": "Drehmoment"})
        self.assertEqual(self.v.cursor.kacheln[2].kanal, "Drehmoment")
        kach[2].waehle("Lambda")
        self.assertEqual(self.v.kachel_wahl, {})

    def test_mittelwert_bilden_speichern_anzeigen(self):
        self.v.add_runs(self.ids[:3])
        pump()
        for rid in self.ids[:2]:
            self.v.leiste.zeilen[rid]["mark"].setChecked(True)
        self.assertEqual(self.v.leiste.markierte(), self.ids[:2])
        self.assertTrue(self.v.leiste.mittelwert_btn.isEnabled())
        self.assertIn("(2 markiert)", self.v.leiste.mittelwert_btn.text())
        quellen = [(self.v.rows[r], self.v.data[r][0], self.v.data[r][1], self.v._basis(self.v.rows[r]))
                   for r in self.ids[:2]]
        dlg = MittelwertDialog(self.db, quellen, ["hauptduese", "clip", "zzp"], self.v)
        self.assertTrue(dlg.ok.isEnabled())
        self.assertFalse(dlg.warnung.isVisibleTo(dlg))                   # gleiches Setup
        self.assertEqual(dlg.name.text(), "Ø 2 Läufe · Serie HD 135")
        # der fruehere, wirkungslose Haken „im Messfenster als Referenz anbieten“ ist weg (1.4.2)
        self.assertFalse(hasattr(dlg, "referenz"))
        self.assertFalse([c for c in dlg.findChildren(QtWidgets.QCheckBox) if "Referenz" in c.text()])
        self.assertEqual(dlg.setup.currentData(), self.s1)
        dlg.baum.topLevelItem(1).setCheckState(0, QtCore.Qt.Unchecked)    # nur noch 1 Lauf
        self.assertFalse(dlg.ok.isEnabled())
        self.assertIn("mindestens 2", dlg.fehler_label.text())
        dlg.baum.topLevelItem(1).setCheckState(0, QtCore.Qt.Checked)
        dlg.band_knoepfe["sigma"].setChecked(True)
        dlg.raster.setValue(100)
        dlg._speichern()
        rid = dlg.rid
        row = self.db.run(rid)
        self.assertTrue(row.ist_mittelwert)
        self.assertEqual(row.quellen, self.ids[:2])
        self.assertEqual(row.werte, {"hauptduese": "135", "clip": "3", "zzp": "18"})
        self.assertEqual(zusatz_lesen(row.ordner)["band"], "sigma")
        self.assertEqual(mw.laden(row).raster, 100.0)
        self.v.mittelwert_uebernehmen(rid, self.ids[:2])
        pump()
        self.assertNotIn(rid, self.geladen)                               # kein Rohdaten-Laden ueber das Hauptfenster
        self.assertEqual(self.v.leiste.eintraege[0].rid, rid)
        self.assertEqual(self.v.leiste.zeilen[rid]["knopf"].text(), "Quellen ▾")
        self.assertFalse(self.v.leiste.zeilen[rid]["mark"].isEnabled())
        self.assertEqual(self.v.markiert, set())
        self.assertIn("Leistung", self.v.data[rid][0])
        np.testing.assert_allclose(self.v.data[rid][0]["Leistung"].values,
                                   self.v.data[rid][0]["Drehzahl Motor"].values / 300.0
                                   + (self.ids[0] + self.ids[1]) / 4.0, rtol=1e-5)   # CSV mit 6 Stellen
        import pyqtgraph as pg
        baender = [it for pi in self.v.plots for it in pi.items if isinstance(it, pg.FillBetweenItem)]
        self.assertEqual(len(baender), 3)                                 # Leistung, Drehmoment, Lambda (σ)
        self.assertEqual(self.v.bez[rid], "Ø Ref.")
        # Cursorwerte: Mittelwert interpoliert
        self.v._set_cursor_x(6000)
        self.v._update_cursor()
        texte = dict(self.v.cursor.kacheln[0].lauf_texte())
        x_at = self.v.cursor_x()
        self.assertEqual(texte["Ø Ref."], f"{x_at / 300.0 + (self.ids[0] + self.ids[1]) / 4.0:.1f}".replace(".", ","))
        # Quellen-Menue, Band umschalten, kein Band
        m = self.v._quellen_menue(rid)
        texte = [a.text() for a in m.actions()]
        self.assertTrue(any("16:18" in t for t in texte))
        self.assertIn("± σ", texte)
        self.v._band_setzen(rid, "")
        self.assertEqual(zusatz_lesen(row.ordner)["band"], "")
        baender = [it for pi in self.v.plots for it in pi.items if isinstance(it, pg.FillBetweenItem)]
        self.assertEqual(baender, [])
        # Auswahlliste links findet Mittelwerte
        self.v._fill_pick()
        ids = [self.v.pick_tree.topLevelItem(i).data(0, QtCore.Qt.UserRole)
               for i in range(self.v.pick_tree.topLevelItemCount())]
        self.assertIn(rid, ids)

    def test_mittelwert_ohne_rohdaten_csv_und_aktueller_lauf(self):
        self.v.add_runs(self.ids[:2])
        pump()
        quellen = [(self.v.rows[r], self.v.data[r][0], self.v.data[r][1], "#1f77b4") for r in self.ids[:2]]
        dlg = MittelwertDialog(self.db, quellen, [], self.v)
        dlg._speichern()
        self.v.mittelwert_uebernehmen(dlg.rid)
        pump()
        self.v.sel_list.setCurrentRow(0)                                  # Mittelwert markiert
        self.assertEqual(self.v._markierter(), dlg.rid)
        self.assertEqual(self.v.current_run_id(), self.ids[0])            # Hauptfenster bekommt keinen Mittelwert
        k = self.v.mittelwert_kanaele(dlg.rid)
        self.assertIn("Leistung min", k)
        self.assertIn("Leistung σ", k)
        geklickt = []
        self.v.csv_btn.clicked.connect(lambda: geklickt.append(1))
        self.v.sel_list.setCurrentRow(1)                                  # normaler Lauf -> Hauptfenster
        self.v.csv_knopf.click()
        self.assertEqual(geklickt, [1])
        # X-Achse Zeit: Mittelwert hat keine Zeit -> Hinweis, kein Absturz
        self.v.xaxis.setCurrentIndex(0)
        self.v._update_cursor()
        self.assertIn("Ø-Läufe", self.v.hint.text())

    def test_setup_dialog_aendert_setup_und_schnappschuss(self):
        self.v.add_runs(self.ids)
        pump()
        row = self.db.run(self.ids[2])
        d = LaufSetupDialog(self.db, row, self.v._vorher(self.ids[2]), self.v)
        self.assertEqual(d.anders_felder(), ["hauptduese"])               # 140 statt 135 beim Lauf davor
        self.assertIn("dieser und 1 weitere", d.hinweis.text())
        d.form.forms[1].widgets["clip"].setText("2")                     # Gruppe Vergaser
        d.notiz.setText("Clip 2")
        d._speichern()
        self.assertTrue(d.geaendert)
        self.assertEqual(self.db.setup(self.s2)["clip"], "2")
        neu = self.db.run(self.ids[2])
        self.assertEqual(neu.werte["clip"], "2")
        self.assertEqual(neu.notiz, "Clip 2")
        self.assertEqual(self.db.run(self.ids[3]).werte["clip"], "3")    # Schnappschuss des anderen Laufs bleibt

    def test_setup_zuordnen(self):
        row = self.db.run(self.ids[0])
        d = LaufSetupDialog(self.db, row, None, self.v)
        d.setup.setCurrentIndex(d.setup.findData(self.s2))
        d._speichern()
        neu = self.db.run(self.ids[0])
        self.assertEqual(neu.setup_id, self.s2)
        self.assertEqual(neu.werte["hauptduese"], "140")

    def test_seitenleiste_zustand_gespeichert(self):
        self.v.seitenleiste.set_angepinnt(True)
        self.assertTrue(self.v._meta_json("viewer_seitenleiste", {}).get("angepinnt"))
        v2 = LogViewer(self.db, synth_kanaele)
        self.assertTrue(v2.seitenleiste.ist_angepinnt())
        v2.deleteLater()

    def test_altes_layout_und_teiler(self):
        self.db.set_meta("viewer_layouts", '{"alt": {"felder": 2, "x": "Drehzahl Motor", "nur_kurve": false, '
                                           '"kanaele": [["Leistung", "Drehmoment"], ["Lambda"], [], []], '
                                           '"anzeigen_aus": ["egt2"]}}')
        self.db.set_meta("viewer_layout_name", "alt")
        self.v._fill_layout_combo()
        self.v._layout_chosen()
        self.assertFalse(self.v.layout_save_btn.isEnabled())             # alte Schluessel machen es nicht "geaendert"
        old = QtWidgets.QSplitter()
        for _ in range(3):
            old.addWidget(QtWidgets.QWidget())
        old.resize(1700, 800)
        old.setSizes([300, 900, 320])
        self.v.split.restoreState(old.saveState())
        self.assertLessEqual(self.v.split.sizes()[1], self.v.split.RECHTS_MAX)

    def test_themenwechsel(self):
        self.v.add_runs(self.ids[:2])
        pump()
        for th in (theme.NACHT, theme.TAG):
            theme.manager().setze(th, speichern=False)
            pump()
            self.v._update_cursor()
            self.assertEqual(self.v.glw.backgroundBrush().color().name(), theme.farbe("plot", th))


if __name__ == "__main__":
    unittest.main()
