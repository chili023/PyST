"""PyST 1.4.2, Testrueckmeldung vom 10.11.: nach dem Lauf das Fenster „Lauf fertig“ (Notiz und Setup-Werte, Speichern
ohne Auto-Speichern bzw. Übernehmen mit), der Knopf „Lauf speichern“ unten rechts und die einklappbare Zeile
SETUP-WERTE."""
import gc
import os
import sys
import time
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtWidgets  # noqa: E402

from pyst import messen_page as mp, theme  # noqa: E402
from pyst.statusleiste import SpeichernKnopf  # noqa: E402

APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
_FERTIG = {}                                       # RunController eines fertigen Simulator-Laufs (einmal gemessen)


def pump(sec: float = 0.0):
    end = time.time() + sec
    while True:
        APP.processEvents()
        if time.time() >= end:
            return
        time.sleep(0.01)


def warte(bedingung, sec: float = 6.0) -> bool:
    end = time.time() + sec
    while time.time() < end:
        if bedingung():
            return True
        pump(0.03)
    return bool(bedingung())


# ============================================================================================== Bausteine
class Fenster(unittest.TestCase):
    def tearDown(self):
        gc.collect()
        APP.processEvents()

    def test_gemeinsames_formular(self):
        """StartNachfrage und LaufFertig bauen auf SetupFenster/SetupFormular auf (kein kopierter Code)."""
        a = mp.StartNachfrage([("hauptduese", "135")], "Serie")
        b = mp.LaufFertig([("hauptduese", "135")], "Lauf fertig: 31,6 PS")
        for d in (a, b):
            self.assertIsInstance(d, mp.SetupFenster)
            self.assertIsInstance(d.formular, mp.SetupFormular)
            self.assertIs(d.edits, d.formular.edits)
        self.assertTrue(a.isModal())
        self.assertFalse(b.isModal())
        for d in (a, b):
            d.deleteLater()

    def test_lauf_fertig_ohne_autosave(self):
        """Ohne Auto-Speichern: Verwerfen | Später | Speichern (Standard); Esc = Später; Notiz vorbelegt; die Leistung
        steht gross im Fenster; Name des neuen Setups vorgeschlagen und aenderbar."""
        d = mp.LaufFertig([("hauptduese", "135"), ("clip", "3")], "Lauf 16:29  ·  Serie, HD 135",
                          "Aprilia RS 125  ·  noch nicht gespeichert", notiz="kalt",
                          basis={"hauptduese": "135", "clip": "3"}, neues_setup=True, setup_name="Serie, HD 135",
                          leistung=("#1f77b4", 31.6, 9850.0, 22.7, 8900.0))
        wahl = []
        d.gewaehlt.connect(wahl.append)
        self.assertEqual(d.windowTitle(), "Lauf fertig")
        self.assertEqual(d.leistung.kopf.text(), "Lauf 16:29  ·  Serie, HD 135")
        self.assertIn("31,6 PS bei 9.850 1/min", d.leistung.werte_text())
        self.assertIn("kW", d.leistung.werte_text())
        self.assertIn("22,7 Nm bei 8.900 1/min", d.leistung.werte_text())
        self.assertFalse(d.frage.isVisibleTo(d))
        self.assertEqual(d.notiz.text(), "kalt")
        self.assertEqual([b.text() for b in (d.b_weg, d.b_spaeter, d.b_ok)], ["Verwerfen", "Später", "Speichern"])
        self.assertTrue(d.b_ok.isDefault())
        self.assertFalse(d.geaendert())
        self.assertFalse(d.neu_setup.isEnabled())              # Werte wie im Setup
        self.assertFalse(d.neu_name.isEnabled())
        d.edits["hauptduese"].setText("140")
        d.edits["hauptduese"].textEdited.emit("140")
        self.assertTrue(d.neu_setup.isEnabled())
        self.assertEqual(d.neu_name.text(), "Serie, HD 140")    # Vorschlag folgt den Werten
        d.edits["hauptduese"].setText("135")
        d.edits["hauptduese"].textEdited.emit("135")
        d.edits["clip"].setText("4")
        d.edits["clip"].textEdited.emit("4")
        self.assertTrue(d.neu_setup.isEnabled())
        self.assertEqual(d.neu_name.text(), "Serie, HD 135, Clip 4")
        self.assertFalse(d.neues_setup())
        d.neu_name.setText("Clip 4 Test")
        d.neu_name.textEdited.emit("Clip 4 Test")              # Name getippt: Haken an, Vorschlag bleibt weg
        self.assertTrue(d.neues_setup())
        self.assertEqual(d.neuer_name(), "Clip 4 Test")
        d.edits["clip"].setText("5")
        d.edits["clip"].textEdited.emit("5")
        self.assertEqual(d.neuer_name(), "Clip 4 Test")
        d.edits["clip"].setText("4")
        d.edits["clip"].textEdited.emit("4")
        self.assertEqual(d.aenderungen(), {"clip": "4"})
        self.assertIn(theme.tokens()["changed_bg"], d.edits["clip"].styleSheet())
        self.assertTrue(d.geaendert())
        d.show()
        pump(0.05)
        d.reject()                                             # Esc
        self.assertEqual(wahl, [mp.SPAETER])
        self.assertFalse(d.isVisible())
        d.deleteLater()

    def test_lauf_fertig_mit_autosave(self):
        """Lauf schon gespeichert: Schließen | Übernehmen (Standard); Enter = Übernehmen, Esc = Schließen."""
        d = mp.LaufFertig([("hauptduese", "135")], "Lauf fertig", gespeichert=True)
        wahl = []
        d.gewaehlt.connect(wahl.append)
        self.assertEqual(d.b_ok.text(), "Übernehmen")
        self.assertEqual(d.b_zu.text(), "Schließen")
        self.assertTrue(d.b_ok.isDefault())
        self.assertFalse(hasattr(d, "b_weg"))
        self.assertFalse(d.neu_zeile.isVisibleTo(d))           # ohne Fahrzeug kein neues Setup
        d.accept()
        d.reject()
        self.assertEqual(wahl, [mp.UEBERNEHMEN, mp.SCHLIESSEN])
        d.deleteLater()

    def test_speichern_knopf(self):
        k = SpeichernKnopf()
        self.assertFalse(k.isEnabled())
        self.assertEqual(k.toolTip(), "Kein ungespeicherter Lauf")
        k.set_aktiv(True)
        self.assertTrue(k.isEnabled())
        self.assertIn("Ctrl+S", k.toolTip())
        k.set_kurz(True)
        self.assertEqual(k.text(), "Speichern")
        k.set_kurz(False)
        self.assertEqual(k.text(), "Lauf speichern")
        for th in (theme.NACHT, theme.TAG):
            theme.manager().setze(th, speichern=False)
            self.assertFalse(k.icon().isNull())
        k.deleteLater()

    def test_setup_zeile_einklappen(self):
        z = mp.SetupZeile()
        z.set_setup(5, "Serie, HD 135", {"hauptduese": "135", "clip": "3", "zzp": "18"},
                    ["hauptduese", "clip", "zzp"], "Aprilia RS 125", "Vergaser")
        z.resize(1200, 60)
        z.show()
        pump(0.05)
        gemeldet = []
        z.eingeklappt_geaendert.connect(gemeldet.append)
        self.assertTrue(z.titel.text().startswith("▾"))
        z.titel.click()
        self.assertTrue(z.eingeklappt)
        self.assertEqual(gemeldet, [True])
        self.assertTrue(z.titel.text().startswith("▸"))
        self.assertEqual(z.zusammenfassung(), "Hauptdüse 135 · Clip 3 · ZZP 18")
        self.assertTrue(z.kurzinfo.isVisibleTo(z))
        self.assertFalse(z.edits[0].isVisibleTo(z))
        self.assertFalse(z.kurzmarke.isVisibleTo(z))
        z.set_wert("hauptduese", "138")
        self.assertEqual(z.zusammenfassung(), "Hauptdüse 138 · Clip 3 · ZZP 18")
        self.assertTrue(z.kurzmarke.isVisibleTo(z))
        self.assertIn("geändert, ungespeichert", z.kurzmarke.text())
        self.assertFalse(z.b_neu.isVisibleTo(z))               # Knoepfe erst ausgeklappt
        z.set_nur_lauf(True)
        self.assertIn("nur dieser Lauf", z.kurzmarke.text())
        pump(0.05)
        self.assertLessEqual(max(z.lay.heightForWidth(1200), z.minimumHeight()), mp.SetupZeile.HOEHE_ZU + 4)
        z.titel.click()
        self.assertFalse(z.eingeklappt)
        self.assertTrue(z.edits[0].isVisibleTo(z))
        self.assertTrue(z.marke.isVisibleTo(z))
        self.assertEqual(gemeldet, [True, False])
        z.deleteLater()


# ============================================================================================== Hauptfenster
class NachDemLauf(unittest.TestCase):
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
        w = self.w
        w._lauf_fertig_schliessen()
        w.autosave.setChecked(True)                        # Einstellungen gelten fuer alle Tests
        w.settings.update({"autosave": True, mp.NACH_LAUF_FENSTER: True, mp.SETUP_EINGEKLAPPT: False})
        self.gui.save_settings(w.settings)
        if w.link is not None:
            if w.ctrl is not None and w.ctrl.state in self.gui.ACTIVE:
                w.on_abort()
            w.on_connect()
        close_window(w, APP)
        gc.collect()
        APP.processEvents()

    # ------------------------------------------------------------ Hilfen
    def _verbinden(self):
        self.w.page_settings.port_combo.setEditText(self.gui.DEFAULT_SIM_PORT)
        self.assertTrue(self.w.on_connect())

    def _sim_lauf(self):
        """Simulator ganz durch (START bis Ergebnis), danach steht das Fenster „Lauf fertig“."""
        from pyst.runner import DONE
        w = self.w
        w.on_start(nachfrage=False)
        self.assertTrue(warte(lambda: w.ctrl.state == DONE and w._done_handled, 60))
        _FERTIG.setdefault("ctrl", w.ctrl)
        return w.ctrl

    def _ctrl(self):
        """Fertiger Lauf (aus dem ersten Simulator-Lauf dieser Datei, sonst jetzt einer)."""
        if "ctrl" not in _FERTIG:
            w = self.w
            self._verbinden()
            alt = w.autosave.isChecked()
            w.autosave.setChecked(True)
            self._sim_lauf()
            warte(lambda: w.lauf_fertig is not None, 4)
            w._lauf_fertig_schliessen()
            w.autosave.setChecked(alt)
            w.on_connect()
        return _FERTIG["ctrl"]

    def _fertig(self, autosave: bool) -> "mp.LaufFertig":
        """Laufende wie gui._tick mit einem fertigen Lauf (ohne neu zu messen): Auswertung, ggf. Auto-Speichern,
        GAS WEG, dann Ergebnis und Fenster."""
        w = self.w
        ctrl = self._ctrl()
        w.autosave.setChecked(autosave)
        alt_ctrl, alt_link = w.ctrl, w.link
        w.ctrl, w.link = ctrl, alt_link or SimpleNamespace(lost=0)
        try:
            w._finish_run()
        finally:
            w.ctrl, w.link = alt_ctrl, alt_link
        w._update_buttons()
        warte(lambda: w.lauf_fertig is not None, 4)
        return w.lauf_fertig

    def _laeufe(self):
        return [r for r in self.w.db.runs() if not r.ist_mittelwert]

    # ------------------------------------------------------------ Tests
    def test_simulator_lauf_fertig_speichern(self):
        """Simulator ohne Auto-Speichern von START bis Speichern: genau ein Fenster „Lauf fertig“ (nicht modal, Leistung
        gross, Notiz aus der Kopfzeile, Werte des Laufs), kein grosses Ergebnis daneben; Speichern -> Lauf mit Notiz
        und Setup-Werten in der Datenbank."""
        w = self.w
        w.autosave.setChecked(False)
        w.run_note.setText("kalt")
        vorher = len(self._laeufe())
        self._verbinden()
        k = w.statusleiste.speichern_knopf
        self.assertFalse(k.isEnabled())
        self._sim_lauf()
        self.assertTrue(warte(lambda: w.lauf_fertig is not None, 4))
        d = w.lauf_fertig
        self.assertTrue(d.isVisible())
        self.assertFalse(d.isModal())
        self.assertIsNone(APP.activeModalWidget())
        self.assertFalse(w.ergebnis_gross.isVisible())         # ein Fenster: kein Ergebnis-Overlay daneben
        fenster = [x for x in APP.topLevelWidgets() if isinstance(x, QtWidgets.QDialog) and x.isVisible()]
        self.assertEqual(fenster, [d])
        r = w._lauf_ctrl.result
        self.assertIn(f"{mp.zahl(r.p_max, 1)} PS bei {mp.zahl(r.n_pmax)} 1/min", d.leistung.werte_text())
        self.assertIn("Nm bei", d.leistung.werte_text())
        self.assertTrue(d.leistung.kopf.text().startswith("Lauf "))
        self.assertIn("Serie, HD 135", d.leistung.kopf.text())
        g, h = d.frameGeometry(), QtCore.QRect(w.mapToGlobal(QtCore.QPoint(0, 0)), w.size())
        self.assertTrue(h.contains(g), (g, h))                   # ganz im Hauptfenster
        self.assertIn("noch nicht gespeichert", d.unter.text())
        self.assertEqual(d.notiz.text(), "kalt")
        self.assertEqual(d.werte(), {"hauptduese": "135", "clip": "3", "zzp": "18"})
        self.assertTrue(k.isEnabled())
        self.assertTrue(w.speichern_aktion.isEnabled())
        self.assertIn("nicht gespeichert", w.ergebnis_titel.text())
        self.assertEqual(len(self._laeufe()), vorher)
        d.notiz.setText("kalt, Choke halb")
        d.edits["clip"].setText("4")
        d.edits["clip"].textEdited.emit("4")
        d.b_ok.click()
        self.assertIsNone(w.lauf_fertig)
        laeufe = self._laeufe()
        self.assertEqual(len(laeufe), vorher + 1)
        row = max(laeufe, key=lambda r: r.id)
        self.assertEqual(row.notiz, "kalt, Choke halb")
        self.assertEqual((row.vehicle_id, row.setup_id), (self.vid, self.sid))
        self.assertEqual(row.werte["clip"], "4")
        self.assertEqual(row.werte["hauptduese"], "135")
        self.assertTrue(row.nur_lauf)
        clip = [x for x in row.setup_werte if x["feld"] == "clip"][0]
        self.assertEqual(clip["vorher"], "3")
        self.assertFalse(k.isEnabled())
        self.assertEqual(k.toolTip(), "Kein ungespeicherter Lauf")
        self.assertNotIn("nicht gespeichert", w.ergebnis_titel.text())
        self.assertNotIn(mp.LIVE_KEY, w.plot.data)
        self.assertIn(str(row.id), w.plot.data)                 # in der Liste Läufe und im Diagramm
        self.assertFalse(w.setup_zeile.ist_geaendert())         # SETUP-WERTE bleibt fuer den naechsten Lauf

    def test_verwerfen_und_spaeter(self):
        """Verwerfen fragt nicht nach, speichert nichts, die Kurve bleibt bis zum naechsten START (Knopf bleibt
        aktiv); Später schliesst nur – beim naechsten START sagt das Band „Letzter Lauf nicht gespeichert“."""
        w = self.w
        vorher = len(self._laeufe())
        d = self._fertig(autosave=False)
        self.assertIsNotNone(d)
        d.b_weg.click()
        self.assertIsNone(w.lauf_fertig)
        self.assertEqual(len(self._laeufe()), vorher)
        self.assertIn(mp.LIVE_KEY, w.plot.data)
        self.assertTrue(w.statusleiste.speichern_knopf.isEnabled())
        w._start_hinweis_ungespeichert()
        self.assertFalse(w.band.isVisible())                    # verworfen: kein Hinweis
        self.assertFalse(w.statusleiste.speichern_knopf.isEnabled())
        d = self._fertig(autosave=False)
        d.b_spaeter.click()
        self.assertIsNone(w.lauf_fertig)
        self.assertEqual(len(self._laeufe()), vorher)
        self.assertTrue(w.statusleiste.speichern_knopf.isEnabled())
        w._start_hinweis_ungespeichert()
        self.assertTrue(w.band.isVisible())
        self.assertEqual(w.band.text(), "Letzter Lauf nicht gespeichert")

    def test_autosave_uebernehmen(self):
        """Mit Auto-Speichern ist der Lauf schon gespeichert: Übernehmen schreibt Notiz und Setup-Werte hinein,
        mit Haken auch als neues Setup (der Lauf gehoert dann dazu)."""
        w = self.w
        w.run_note.setText("warm")
        d = self._fertig(autosave=True)
        rid = w._lauf_rid
        self.assertIsNotNone(rid)
        self.assertTrue(d.gespeichert)
        self.assertIn("gespeichert ✓", d.unter.text())
        self.assertFalse(w.statusleiste.speichern_knopf.isEnabled())
        self.assertEqual(w.db.run(rid).notiz, "warm")
        d.notiz.setText("warm, neue Kerze")
        d.edits["zzp"].setText("20")
        d.edits["zzp"].textEdited.emit("20")
        d.b_ok.click()
        row = w.db.run(rid)
        self.assertEqual(row.notiz, "warm, neue Kerze")
        self.assertEqual(row.werte["zzp"], "20")
        self.assertTrue(row.nur_lauf)
        self.assertEqual(row.setup_id, self.sid)
        # zweiter Lauf, diesmal mit „auch als neues Setup speichern“
        d = self._fertig(autosave=True)
        rid2 = w._lauf_rid
        self.assertNotEqual(rid2, rid)
        d.edits["hauptduese"].setText("140")
        d.edits["hauptduese"].textEdited.emit("140")
        self.assertTrue(d.neu_setup.isEnabled())
        self.assertEqual(d.neu_name.text(), "Serie, HD 140")    # Vorschlag
        d.neu_name.setText("HD 140, Kerze neu")
        d.neu_name.textEdited.emit("HD 140, Kerze neu")    # selbst benannt, setzt den Haken
        self.assertTrue(d.neu_setup.isChecked())
        d.b_ok.click()
        row = w.db.run(rid2)
        self.assertNotEqual(row.setup_id, self.sid)
        neu = w.db.setup(row.setup_id)
        self.assertEqual(neu["name"], "HD 140, Kerze neu")
        self.assertEqual((neu["hauptduese"], neu["clip"]), ("140", "3"))
        self.assertFalse(row.nur_lauf)
        self.assertEqual(row.werte["hauptduese"], "140")
        self.assertEqual(w.setup_combo.currentData(), row.setup_id)    # neues Setup gewaehlt
        # Schließen aendert nichts
        d = self._fertig(autosave=True)
        rid3 = w._lauf_rid
        d.notiz.setText("nicht übernehmen")
        d.b_zu.click()
        self.assertNotEqual(w.db.run(rid3).notiz, "nicht übernehmen")

    def test_start_bei_offenem_fenster(self):
        """Neuer START (F1 im Fenster, Zangentaste) bei offenem Fenster: es schliesst sich. Mit Auto-Speichern gelten
        getippte Aenderungen, ohne bleibt der Lauf ungespeichert und das Band sagt es."""
        from pyst.runner import ACTIVE
        w = self.w
        self._ctrl()
        self._verbinden()
        d = self._fertig(autosave=True)
        rid = w._lauf_rid
        d.notiz.setText("nachgetragen")
        d.start.emit()                                          # F1 im Fenster
        self.assertIsNone(w.lauf_fertig)
        self.assertIn(w.ctrl.state, ACTIVE)
        self.assertEqual(w.db.run(rid).notiz, "nachgetragen")
        self.assertFalse(w.statusleiste.speichern_knopf.isEnabled())
        w.on_abort()
        pump(0.2)
        vorher = len(self._laeufe())
        d = self._fertig(autosave=False)
        self.assertIsNotNone(d)
        self.assertTrue(w.statusleiste.speichern_knopf.isEnabled())
        self.assertEqual(w._taste_sperre(), "")                 # Fenster nicht modal: Zangentaste geht
        w._taste_lauf()                                         # Zangentaste „Neuer Lauf“
        self.assertIsNone(w.lauf_fertig)
        self.assertIn(w.ctrl.state, ACTIVE)
        self.assertEqual(w.band.text(), "Neuer Lauf – letzter Lauf nicht gespeichert")
        self.assertEqual(len(self._laeufe()), vorher)
        self.assertFalse(w.statusleiste.speichern_knopf.isEnabled())
        w.on_abort()

    def test_knopf_lauf_speichern_ohne_fenster(self):
        """Schalter „Nach dem Lauf Notiz/Setup-Fenster zeigen“ aus: kein Fenster, Knopf und Datei › Lauf speichern
        (Ctrl+S) speichern den Lauf (Notiz aus der Kopfzeile), danach sind beide aus."""
        w = self.w
        self.assertEqual(w.speichern_aktion.shortcut().toString(), "Ctrl+S")
        self.assertFalse(w.speichern_aktion.isEnabled())
        w.page_settings.nach_lauf_fenster.setChecked(False)
        self.assertFalse(self.gui.load_settings()[mp.NACH_LAUF_FENSTER])
        vorher = len(self._laeufe())
        self.assertIsNone(self._fertig(autosave=False))
        self.assertTrue(warte(w.ergebnis_gross.isVisible, 3))   # ohne Fenster wie bisher das grosse Ergebnis
        self.assertIn("nicht gespeichert", w.ergebnis_gross.kopf.text())
        k = w.statusleiste.speichern_knopf
        self.assertTrue(k.isEnabled())
        self.assertTrue(w.speichern_aktion.isEnabled())
        w.run_note.setText("nachher eingetragen")
        k.click()
        laeufe = self._laeufe()
        self.assertEqual(len(laeufe), vorher + 1)
        self.assertEqual(max(laeufe, key=lambda r: r.id).notiz, "nachher eingetragen")
        self.assertFalse(k.isEnabled())
        self.assertFalse(w.speichern_aktion.isEnabled())
        self.assertIsNone(w.lauf_speichern())                   # nichts mehr zu speichern
        self.assertEqual(len(self._laeufe()), vorher + 1)
        # Ctrl+S bei offenem Fenster: wie Speichern im Fenster
        w.page_settings.nach_lauf_fenster.setChecked(True)
        d = self._fertig(autosave=False)
        d.notiz.setText("per Ctrl+S")
        w.speichern_aktion.trigger()
        self.assertIsNone(w.lauf_fertig)
        self.assertEqual(max(self._laeufe(), key=lambda r: r.id).notiz, "per Ctrl+S")

    def test_setup_eingeklappt_nach_neustart(self):
        """Eingeklappte SETUP-WERTE: das Diagramm wird hoeher, der Zustand steht in einstellungen.json und gilt
        nach dem Neustart."""
        w, gui = self.w, self.gui
        z = w.setup_zeile
        pump(0.1)
        h_plot, h_zeile = w.plot.height(), z.height()
        z.titel.click()
        pump(0.1)
        self.assertTrue(gui.load_settings()[mp.SETUP_EINGEKLAPPT])
        self.assertLess(z.height(), h_zeile)
        self.assertGreater(w.plot.height(), h_plot)
        self.assertEqual(z.kurzinfo.text(), "Hauptdüse 135 · Clip 3 · ZZP 18")
        from conftest import close_window
        close_window(w, APP)
        self.w = gui.MainWindow()
        self.w.show()
        pump(0.1)
        self.assertTrue(self.w.setup_zeile.eingeklappt)
        self.w.setup_zeile.titel.click()
        self.assertFalse(gui.load_settings()[mp.SETUP_EINGEKLAPPT])


if __name__ == "__main__":
    unittest.main()
