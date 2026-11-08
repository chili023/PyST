"""Stufe 1 der neuen Oberflaeche: Themen Tag/Nacht/Auto (Kontraste, Laufarben, Umschalten), Statusleiste
(reine Funktion + Widget), gemeinsame Widgets WertKachel und SeitenLeiste, Reiteraufbau des Hauptfensters."""
import os
import sys
import time
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

from pyst import farben, kacheln, statusleiste as sl, theme  # noqa: E402
from pyst.statusleiste import Eingang  # noqa: E402

APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def pump(sec: float = 0.0, cond=None) -> bool:
    end = time.time() + sec
    while True:
        APP.processEvents()
        if cond is not None and cond():
            return True
        if time.time() >= end:
            return cond is None
        time.sleep(0.01)


# ============================================================================================== Thema
class Thema(unittest.TestCase):
    def tearDown(self):
        theme.manager().setze(theme.TAG, speichern=False)

    def test_kontraste_beider_themen(self):
        """Jedes Token-Paar der Kontrasttabelle erfuellt sein Soll (WCAG) – in beiden Themen."""
        for th in (theme.TAG, theme.NACHT):
            for fg, bg, rolle, soll in theme.KONTRAST_PAARE:
                k = theme.kontrast(theme.farbe(fg, th), theme.farbe(bg, th))
                self.assertGreaterEqual(k + 1e-9, soll, f"{th}: {rolle} ({fg} auf {bg}) {k:.2f} < {soll}")
            self.assertEqual(len(theme.kontrast_tabelle(th)), len(theme.KONTRAST_PAARE))

    def test_kontrast_formel(self):
        self.assertAlmostEqual(theme.kontrast("#000000", "#ffffff"), 21.0, places=2)
        self.assertAlmostEqual(theme.kontrast("#777777", "#777777"), 1.0, places=6)

    def test_laufarben_wie_im_entwurf(self):
        erwartet = {  # Basis: (Tag, Nacht) laut Entwurf NOTIZEN.md Runde 3
            "#2ca02c": ("#2ca02c", "#2ca02c"), "#843c39": ("#843c39", "#a44b47"),
            "#bcbd22": ("#91921a", "#bcbd22"), "#d62728": ("#d62728", "#d62728"),
            "#6b6ecf": ("#6b6ecf", "#6b6ecf"), "#17becf": ("#139caa", "#17becf")}
        for basis, (tag, nacht) in erwartet.items():
            self.assertEqual(theme.laufarbe(basis, theme.TAG).lower(), tag, basis)
            self.assertEqual(theme.laufarbe(basis, theme.NACHT).lower(), nacht, basis)
        for th in (theme.TAG, theme.NACHT):
            for basis in __import__("pyst.plots", fromlist=["RUN_COLORS"]).RUN_COLORS:
                k = theme.kontrast(theme.laufarbe(basis, th), theme.farbe("plot", th))
                self.assertGreaterEqual(k + 0.05, theme.LAUF_KONTRAST, (th, basis))
            self.assertEqual(theme.laufarbe("@avg_color", th), theme.farbe("avg_color", th))
            self.assertEqual(theme.laufarbe("#000000", th), theme.farbe("live_curve", th))

    def test_status_farben_und_symbole(self):
        for th in (theme.TAG, theme.NACHT):
            syms = {theme.status_farben(s, th)[2] for s in theme.STUFEN}
            self.assertEqual(syms, {"✔", "⚠", "✖", "–"})
            for s in theme.STUFEN:
                bg, fg, _ = theme.status_farben(s, th)
                self.assertGreaterEqual(theme.kontrast(fg, bg), 4.5, (th, s))

    def test_farben_folgen_dem_thema(self):
        theme.manager().setze(theme.TAG, speichern=False)
        self.assertEqual(farben.GUT, theme.farbe("ok_fg", theme.TAG))
        theme.manager().setze(theme.NACHT, speichern=False)
        self.assertEqual(farben.GUT, theme.farbe("ok_fg", theme.NACHT))
        self.assertEqual(farben.FEHLER, theme.farbe("err_fg", theme.NACHT))
        # alte feste Farben und Farben beider Themen werden einer Stufe zugeordnet
        for farbe, stufe in (("#7ed67e", "ok"), ("#ffe08a", "warn"), ("#ff5555", "err"),
                             (theme.farbe("warn_fg", theme.TAG), "warn"), (theme.farbe("ok_fg", theme.NACHT), "ok"),
                             (farben.INFO, "info")):
            self.assertEqual(farben.stufe_von(farbe), stufe, farbe)
        self.assertEqual(farben.stufe_von(""), "")

    def test_umschalten_palette_signal(self):
        m = theme.manager()
        m.setze(theme.TAG, speichern=False)
        gesehen = []
        m.geaendert.connect(gesehen.append)
        try:
            m.setze(theme.NACHT, speichern=False)
            self.assertEqual((m.wahl, m.aktiv, theme.aktiv()), (theme.NACHT, theme.NACHT, theme.NACHT))
            self.assertTrue(theme.ist_nacht())
            self.assertEqual(APP.palette().color(QtGui.QPalette.Window).name(), theme.farbe("win", theme.NACHT))
            self.assertIn(theme.farbe("panel", theme.NACHT), APP.styleSheet())
            self.assertEqual(APP.font().pixelSize(), theme.SCHRIFT_PX)
            m.setze(theme.TAG, speichern=False)
            self.assertEqual(APP.palette().color(QtGui.QPalette.Window).name(), theme.farbe("win", theme.TAG))
        finally:
            m.geaendert.disconnect(gesehen.append)
        self.assertEqual(gesehen, [theme.NACHT, theme.TAG])

    def test_auto_folgt_dem_system(self):
        self.assertIn(theme.system_thema(), (theme.TAG, theme.NACHT))
        m = theme.manager()
        m.setze(theme.AUTO, speichern=False)
        self.assertEqual(m.wahl, theme.AUTO)
        self.assertEqual(m.aktiv, theme.system_thema())
        unknown = QtCore.Qt.ColorScheme.Unknown
        orig = QtGui.QGuiApplication.styleHints

        class _Hints:
            def colorScheme(self):
                return unknown
        try:
            QtGui.QGuiApplication.styleHints = staticmethod(lambda: _Hints())
            self.assertEqual(theme.system_thema(), theme.TAG)          # unbekannt -> Tag
        finally:
            QtGui.QGuiApplication.styleHints = orig

    def test_bei_wechsel_und_geloeschte_widgets(self):
        """Rueckruf beim Wechsel; ein geloeschtes Widget wird abgehaengt statt aufgerufen."""
        m = theme.manager()
        w = QtWidgets.QLabel()
        seen = []
        theme.bei_wechsel(w, seen.append, sofort=True)
        self.assertEqual(seen, [m.aktiv])
        m.setze(theme.NACHT, speichern=False)
        self.assertEqual(seen[-1], theme.NACHT)
        w.deleteLater()
        APP.sendPostedEvents(None, QtCore.QEvent.DeferredDelete.value)
        pump(0.05)
        n = len(seen)
        m.setze(theme.TAG, speichern=False)                          # darf nicht mehr rufen oder abstuerzen
        self.assertEqual(len(seen), n)

    def test_diagramm_im_thema(self):
        from pyst import plots
        p = plots.DynoPlot()
        import numpy as np
        n = np.linspace(3000, 9000, 50)
        p.set_curves("r1", n, n / 400, n / 900, color="#bcbd22")
        theme.manager().setze(theme.NACHT, speichern=False)
        self.assertEqual(p.backgroundBrush().color().name(), theme.farbe("plot", theme.NACHT))
        pen = p.items["r1"]["ps"][0].opts["pen"]
        self.assertEqual(QtGui.QPen(pen).color().name(), theme.laufarbe("#bcbd22", theme.NACHT))
        theme.manager().setze(theme.TAG, speichern=False)
        self.assertEqual(p.backgroundBrush().color().name(), theme.farbe("plot", theme.TAG))
        self.assertEqual(QtGui.QPen(p.items["r1"]["ps"][0].opts["pen"]).color().name(), "#91921a")
        p.deleteLater()
        pump(0.05)


# ============================================================================================== Statusleiste
class StatusLogik(unittest.TestCase):
    def zange(self, **kw):
        z = dict(id=1, connected=True, rssi=-58.0, akku_mv=3900.0, akku_pct=80.0, vbus=False, sync_ok=True)
        z.update(kw)
        return SimpleNamespace(**z)

    def lam(self, st="aus", low=False, rest=0):
        return SimpleNamespace(known=True, st=st, low_voltage=low, rest_s=rest)

    def test_nichts_verbunden(self):
        z = sl.zustaende(Eingang())
        self.assertEqual(z["basis"], ("err", "nicht verbunden"))
        self.assertEqual(z["zange"][0], "aus")
        self.assertEqual(z["ecu"], ("aus", "ECU nicht verbunden"))

    def test_basis_mit_zange(self):
        e = Eingang(verbunden=True, board="basis", port="/dev/ttyACM0", fw="0.4.0", hz=60.0, zange_state="ok",
                    zange=self.zange(), lam=self.lam("heizt"), zangen_weitere=1)
        z = sl.zustaende(e)
        self.assertEqual(z["basis"], ("ok", "verbunden USB · FW 0.4.0 · 60 Hz"))
        self.assertEqual(z["zange"], ("ok", "Zange 1: Funk ok (−58 dBm) · Akku 3,9 V · +1 weitere"))
        self.assertEqual(z["ecu"], ("ok", "Lambda heizt · ECU nicht verbunden"))
        e.port = "tcp://192.168.4.1:2323"
        self.assertTrue(sl.basis_zustand(e)[1].startswith("verbunden WLAN"))

    def test_warnungen_und_fehler(self):
        e = Eingang(verbunden=True, board="basis", zange_state="ok", zange=self.zange(rssi=-85.0))
        self.assertEqual(sl.zange_zustand(e), ("warn", "Zange 1: Funk schwach (−85 dBm) · Akku 3,9 V"))
        e.zange = self.zange(akku_mv=float("nan"), akku_pct=12.0)
        st, text = sl.zange_zustand(e)
        self.assertEqual(st, "warn")
        self.assertIn("Akku 12 %", text)
        self.assertIn("laden!", text)
        e.zange = self.zange(sync_ok=False)
        self.assertEqual(sl.zange_zustand(e)[0], "warn")
        e.zange_state = "weg"
        self.assertEqual(sl.zange_zustand(e)[0], "err")
        e.zange_state = "usb"
        self.assertEqual(sl.zange_zustand(e)[0], "warn")
        # Lambda und ECU zusammen: die schwerere Stufe zaehlt
        e = Eingang(verbunden=True, board="basis", lam=self.lam("scharf", rest=125))
        self.assertEqual(sl.lambda_ecu_zustand(e), ("warn", "Lambda bereit · aus in 2:05 · ECU nicht verbunden"))
        e.lam = self.lam("heizt", low=True)
        e.ecu = {"fehler": "", "ini": True, "passt": True, "hz": 50.0}
        self.assertEqual(sl.lambda_ecu_zustand(e), ("err", "Lambda: 12 V zu niedrig · ECU verbunden · 50 Hz"))
        e.lam = self.lam("aus")
        e.ecu = {"fehler": "", "ini": False}
        self.assertEqual(sl.lambda_ecu_zustand(e)[0], "warn")
        e.ecu = {"fehler": "Zeitueberschreitung"}
        self.assertEqual(sl.lambda_ecu_zustand(e)[0], "err")
        self.assertEqual(sl.schlimmste("", "ok", "warn"), "warn")
        self.assertEqual(sl.schlimmste(), "aus")

    def test_messboard_und_bootloader(self):
        e = Eingang(verbunden=True, board="f446", port="SIM:stm32", fw="PST-STM32 SIMULATOR 1.1.0;", hz=20.0)
        z = sl.zustaende(e)
        self.assertEqual(z["basis"][0], "aus")
        self.assertIn("STM32 (F446)", z["basis"][1])
        self.assertEqual(z["zange"], ("ok", "STM32 (F446) verbunden Simulator · FW 1.1.0 · 20 Hz"))
        e = Eingang(verbunden=True, board="basis", bootloader=True)
        self.assertEqual(sl.basis_zustand(e)[0], "warn")
        self.assertEqual(sl.basis_zustand(Eingang(anmeldung_fehler="x"))[0], "err")

    def test_anzeige(self):
        a = sl.anzeige({"basis": ("ok", "verbunden"), "zange": ("quatsch", "?")}, theme.NACHT)
        self.assertEqual([x.schluessel for x in a], ["basis", "zange", "ecu"])
        self.assertEqual((a[0].stufe, a[0].symbol), ("ok", "✔"))
        self.assertEqual((a[0].hintergrund, a[0].schrift), theme.status_farben("ok", theme.NACHT)[:2])
        self.assertEqual(a[1].stufe, "aus")                          # unbekannte Stufe -> aus
        self.assertEqual((a[2].stufe, a[2].text), ("aus", "–"))      # fehlendes Feld


class StatusWidget(unittest.TestCase):
    def test_felder_und_umschalter(self):
        leiste = sl.Statusleiste()
        self.assertEqual(leiste.height(), sl.HOEHE)
        leiste.set_zustaende({"basis": ("err", "nicht verbunden"), "zange": ("ok", "Zange 1: Funk ok"),
                              "ecu": ("warn", "Lambda bereit")})
        f = leiste.feld("basis")
        self.assertEqual((f.stufe, f.text(), f.symbol.text()), ("err", "nicht verbunden", "✖"))
        self.assertIn("PST Basis", f.toolTip())
        self.assertIn(theme.status_farben("err")[0], f.styleSheet())
        self.assertEqual(leiste.feld("ecu").symbol.text(), "⚠")
        m = theme.manager()
        m.setze(theme.TAG, speichern=False)
        leiste.umschalter.knoepfe[theme.NACHT].click()
        self.assertEqual(m.aktiv, theme.NACHT)
        self.assertTrue(leiste.umschalter.knoepfe[theme.NACHT].isChecked())
        self.assertIn(theme.status_farben("err", theme.NACHT)[0], f.styleSheet())     # Feld folgt dem Thema
        leiste.umschalter.knoepfe[theme.TAG].click()
        self.assertEqual(m.aktiv, theme.TAG)
        sb = QtWidgets.QStatusBar()
        leiste.einbauen(sb)
        sb.showMessage("Lauf gespeichert")
        self.assertEqual(leiste.meldung.text(), "Lauf gespeichert")
        sb.deleteLater()
        pump(0.05)


# ============================================================================================== WertKachel
KANAELE = [kacheln.Kanal("egt1", "EGT 1", "°C", "PST", 0, 200, 1000),
           kacheln.Kanal("lambda", "Lambda", "λ", "PST", 2, 0.6, 1.4),
           kacheln.Kanal("ecu:MAPValue", "MAP", "kPa", "ECU", 0, 0, 120)]


class Kachel(unittest.TestCase):
    def test_menue_gruppiert_und_leer_lassen(self):
        k = kacheln.WertKachel(platz=2, kanaele=KANAELE)
        k.set_menue_hinweis("Auswahl gilt für Aprilia RS 125")
        gewaehlt = []
        k.kanal_gewaehlt.connect(gewaehlt.append)
        k._menue_fuellen()
        acts = k.menue.actions()
        sektionen = [a.text() for a in acts if a.isSeparator() and a.text()]
        self.assertEqual(sektionen, ["PST", "ECU"])
        texte = [a.text() for a in acts if not a.isSeparator()]
        self.assertEqual(texte, ["EGT 1 [°C]", "Lambda [λ]", "MAP [kPa]", "leer lassen",
                                 "Auswahl gilt für Aprilia RS 125"])
        self.assertFalse(acts[-1].isEnabled())
        next(a for a in acts if a.text() == "MAP [kPa]").trigger()
        self.assertEqual((gewaehlt, k.kanal), (["ecu:MAPValue"], "ecu:MAPValue"))
        self.assertEqual(k.quelle.text(), "ECU")
        self.assertIn("MAP", k.titel.text())
        k._menue_fuellen()
        self.assertTrue(next(a for a in k.menue.actions() if a.text() == "MAP [kPa]").isChecked())
        next(a for a in k.menue.actions() if a.text() == "leer lassen").trigger()
        self.assertEqual(gewaehlt[-1], kacheln.LEER)
        self.assertTrue(k.titel.text().startswith("leer"))
        self.assertTrue(k.balken.isHidden())
        self.assertEqual(k.nr.text(), "2")

    def test_wert_bereich_fehlt(self):
        k = kacheln.WertKachel(platz=1, kanaele=KANAELE)
        k.set_kanal("egt1")
        k.set_bereich(200, 1000, zonen=[(850, 899, "warn"), (900, 1000, "err")])
        k.set_wert(846)
        self.assertEqual((k.wert_text(), k.einheit.text()), ("846", "°C"))
        self.assertEqual(k.balken.zonen_stufe(870), "warn")
        self.assertEqual(k.balken.zonen_stufe(950), "err")
        self.assertEqual(k.balken.wert, 846)
        k.set_wert(float("nan"))
        self.assertEqual(k.wert_text(), "–")
        k.set_fehlt("ECU fehlt")
        self.assertTrue(k.fehlt)
        self.assertEqual((k.quelle.text(), k.wert_text()), ("ECU fehlt", "–"))
        self.assertTrue(k.balken.fehlt)
        k.set_wert(850)
        self.assertFalse(k.fehlt)
        self.assertEqual(k.quelle.text(), "PST")
        k.set_kanal("lambda")
        k.set_wert(0.934)
        self.assertEqual(k.wert_text(), "0,93")
        klein = kacheln.WertKachel(groesse=kacheln.KLEIN, kanaele=KANAELE)
        klein.set_kanal("egt1")
        self.assertLess(klein.balken.height() or klein.balken.sizeHint().height(),
                        k.balken.height() or k.balken.sizeHint().height() + 1)

    def test_mehrere_laeufe(self):
        k = kacheln.WertKachel(kanaele=KANAELE)
        k.set_kanal("egt1")
        k.set_bereich(200, 1000)
        k.set_laeufe([("#d62728", "16:24", 812.0), ("#2ca02c", "16:31", None)])
        self.assertEqual(k.lauf_texte(), [("16:24", "812"), ("16:31", "–")])
        self.assertTrue(k.wert_zeile.isHidden())
        self.assertFalse(k.lauf_box.isHidden())
        self.assertEqual(k.balken.marken, [(812.0, "#d62728")])
        self.assertEqual(k.lauf_grid.rowCount() >= 2, True)
        k.set_laeufe([])
        self.assertFalse(k.wert_zeile.isHidden())
        self.assertTrue(k.lauf_box.isHidden())

    def test_zahl(self):
        self.assertEqual(kacheln.zahl(1234.5, 1), "1.234,5")
        self.assertEqual(kacheln.zahl(None), "–")
        self.assertEqual(kacheln.zahl(float("nan")), "–")


# ============================================================================================== SeitenLeiste
class Leiste(unittest.TestCase):
    def setUp(self):
        self.fenster = QtWidgets.QWidget()
        self.fenster.resize(1000, 600)
        lay = QtWidgets.QVBoxLayout(self.fenster)
        self.inhalt = QtWidgets.QListWidget()
        self.inhalt.addItems(["Lauf 1", "Lauf 2"])
        self.nachbar = QtWidgets.QLabel("Diagramm")
        self.leiste = kacheln.SeitenLeiste("Läufe", self.inhalt, self.nachbar, breite=300)
        lay.addWidget(self.leiste)
        self.fenster.show()
        pump(0.05)

    def tearDown(self):
        self.leiste.schliessen()
        self.fenster.close()
        self.fenster.deleteLater()
        pump(0.05)

    def test_overlay_nach_verzoegerung_ohne_den_nachbarn_zu_schieben(self):
        L = self.leiste
        self.assertEqual(L.streifen.width(), 32)
        self.assertFalse(L.ist_offen())
        breite_vorher = self.nachbar.width()
        L._maus_drin = lambda: True                       # offscreen gibt es keinen Mauszeiger
        QtWidgets.QApplication.sendEvent(L.streifen, QtGui.QEnterEvent(QtCore.QPointF(5, 5), QtCore.QPointF(5, 5),
                                                                      QtCore.QPointF(5, 5)))
        self.assertTrue(L.auf_timer.isActive())
        self.assertEqual(L.auf_timer.interval(), 300)
        pump(0.1)
        self.assertFalse(L.ist_offen())                   # noch nicht nach 100 ms
        self.assertTrue(pump(1.0, L.ist_offen))
        self.assertTrue(L.feld.isVisible())
        self.assertIs(L.feld.parentWidget(), L)           # Overlay, nicht im Teiler
        self.assertEqual(self.nachbar.width(), breite_vorher)
        self.assertEqual(L.feld.width(), 300)
        pump(0.5)
        self.assertTrue(L.ist_offen())                    # Maus drin: bleibt offen

    def test_klick_daneben_und_maus_weg_schliessen(self):
        L = self.leiste
        L._maus_drin = lambda: True
        zu = []
        L.offen_geaendert.connect(zu.append)
        L.oeffnen()
        self.assertTrue(L.ist_offen())
        pos = QtCore.QPointF(self.nachbar.width() - 5, 10)
        ev = QtGui.QMouseEvent(QtCore.QEvent.MouseButtonPress, pos, self.nachbar.mapToGlobal(pos),
                               QtCore.Qt.LeftButton, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier)
        QtWidgets.QApplication.sendEvent(self.nachbar, ev)
        self.assertFalse(L.ist_offen())
        self.assertEqual(zu, [True, False])
        # Klick in die Liste schliesst nicht
        L.oeffnen()
        QtWidgets.QApplication.sendEvent(self.inhalt.viewport(), QtGui.QMouseEvent(
            QtCore.QEvent.MouseButtonPress, QtCore.QPointF(5, 5), QtCore.QPointF(5, 5), QtCore.Qt.LeftButton,
            QtCore.Qt.LeftButton, QtCore.Qt.NoModifier))
        self.assertTrue(L.ist_offen())
        # Maus verlaesst das Feld -> nach ZU_MS zu
        L._maus_drin = lambda: False
        QtWidgets.QApplication.sendEvent(L.feld, QtCore.QEvent(QtCore.QEvent.Leave))
        self.assertTrue(pump(1.0, lambda: not L.ist_offen()))

    def test_anpinnen_und_zustand(self):
        L = self.leiste
        an = []
        L.angepinnt_geaendert.connect(an.append)
        L.pin.click() if L.feld.isVisible() else L.set_angepinnt(True)
        pump(0.05)
        self.assertTrue(L.ist_angepinnt() and L.ist_offen())
        self.assertEqual(L.splitter.indexOf(L.feld), 0)   # im Teiler: schiebt den Nachbarn
        self.assertTrue(L.streifen.isHidden())
        self.assertLess(self.nachbar.width(), 1000 - 250)
        self.assertEqual(L.pin.text(), "angepinnt")
        self.assertEqual(L.zustand(), {"angepinnt": True, "breite": 300})
        L.pin.click()                                     # loesen
        pump(0.05)
        self.assertFalse(L.ist_angepinnt() or L.ist_offen())
        self.assertEqual(L.splitter.indexOf(L.feld), -1)
        self.assertFalse(L.streifen.isHidden())
        self.assertEqual(an, [True, False])
        L.set_zustand({"angepinnt": True, "breite": 250})
        self.assertTrue(L.ist_angepinnt())
        self.assertEqual(L.zustand()["breite"], 250)
        L.set_streifen_text("Läufe (3 von 6 sichtbar)")
        L.set_streifen_farben(["#d62728", "#2ca02c"])
        self.assertEqual(L.streifen.text(), "Läufe (3 von 6 sichtbar)")


# ============================================================================================== Hauptfenster
class Hauptfenster(unittest.TestCase):
    def test_reiter_menue_umschalter(self):
        from conftest import close_window
        from pyst import gui
        w = gui.MainWindow()
        try:
            self.assertEqual([w.tabs.tabText(i) for i in range(w.tabs.count())],
                             ["Messen", "Fahrzeuge && Setups", "Auswertung", "Oszi / Dauermessung", "Einstellungen"])
            self.assertEqual(set(w.seiten), {"messen", "fahrzeuge", "auswertung", "oszi", "rolle", "verluste",
                                             "verbindung", "pruefen", "zange", "tasten", "lambda", "ecu", "firmware",
                                             "wifi"})
            for key, page in w.seiten.items():                 # jede Seite laesst sich zeigen
                w.show_page(page)
                self.assertEqual(w.current_page_key(), key)
            self.assertEqual(set(w.statusleiste.felder), {"basis", "zange", "ecu"})
            self.assertEqual(w.statusleiste.feld("basis").stufe, "err")
            w.thema_aktionen[theme.NACHT].trigger()
            self.assertEqual((theme.manager().aktiv, gui.load_settings()["thema"]), (theme.NACHT, "nacht"))
            self.assertTrue(w.statusleiste.umschalter.knoepfe[theme.NACHT].isChecked())
            w.statusleiste.umschalter.knoepfe[theme.AUTO].click()
            self.assertEqual(gui.load_settings()["thema"], "auto")
            self.assertTrue(w.thema_aktionen[theme.AUTO].isChecked())
            w.statusleiste.umschalter.knoepfe[theme.TAG].click()
            self.assertEqual(gui.load_settings()["thema"], "tag")
        finally:
            close_window(w, APP)
            theme.manager().setze(theme.TAG, speichern=False)


if __name__ == "__main__":
    unittest.main()
