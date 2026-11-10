"""PyST 1.4.2: Statusleiste mit Piktogramm-Knoepfen Tag/Nacht/Auto und Knopf „PDF-Bericht“ (bericht.py: was im
Diagramm steht, auch der automatische Mittelwert und Referenzen), Ordner-Import mit Unterordnern (ordner_import.py:
Suche, Vorschau, Duplikate, Zuordnung, Ergebnisbericht)."""
import gc
import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

from pyst import bericht, ordner_import as oi, statusleiste as sl, storage, theme  # noqa: E402
from pyst.db import Database  # noqa: E402

APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
DATA = os.path.join(os.path.dirname(__file__), "data")
XMLS = sorted(f for f in os.listdir(DATA) if f.endswith(".xml"))
LOG = os.path.join(DATA, "terminal_mega_beispiel.log")


def pump(sec: float = 0.0):
    end = time.time() + sec
    while True:
        APP.processEvents()
        if time.time() >= end:
            return
        time.sleep(0.01)


# ============================================================================================== Statusleiste
class Statusleiste(unittest.TestCase):
    def tearDown(self):
        theme.manager().setze(theme.TAG, speichern=False)
        gc.collect()
        APP.processEvents()

    def test_piktogramme_schmal_und_pdf_knopf(self):
        leiste = sl.Statusleiste()
        u = leiste.umschalter
        self.assertEqual({w: b.text() for w, b in u.knoepfe.items()},
                         {theme.TAG: "☀", theme.NACHT: "☾", theme.AUTO: "◐"})
        for b in u.knoepfe.values():
            self.assertLessEqual(b.width(), sl.KNOPF_PX)
            self.assertIn("Darstellung", b.toolTip())
        self.assertIn("Nacht (dunkel)", u.knoepfe[theme.NACHT].toolTip())
        self.assertIn("Automatisch", u.knoepfe[theme.AUTO].toolTip())
        self.assertLessEqual(u.sizeHint().width(), 3 * sl.KNOPF_PX + 2)    # frueher rund 220 px
        m = theme.manager()
        m.setze(theme.TAG, speichern=False)
        u.knoepfe[theme.NACHT].click()
        self.assertEqual(m.aktiv, theme.NACHT)
        self.assertTrue(u.knoepfe[theme.NACHT].isChecked())
        self.assertIn(theme.tokens()["accent"], u.knoepfe[theme.NACHT].styleSheet())     # Wahl hervorgehoben
        self.assertNotIn(theme.tokens()["accent"] + ";", u.knoepfe[theme.TAG].styleSheet())
        u.knoepfe[theme.TAG].click()
        self.assertEqual(m.aktiv, theme.TAG)
        # PDF-Knopf: Text, Symbol, Signal
        k = leiste.pdf_knopf
        self.assertEqual(k.text(), "PDF-Bericht")
        self.assertFalse(k.icon().isNull())
        gemeldet = []
        leiste.pdf_gewuenscht.connect(lambda: gemeldet.append(1))
        k.click()
        self.assertEqual(gemeldet, [1])
        lay = leiste.layout()
        self.assertLess(lay.indexOf(k), lay.indexOf(u))                     # PDF links neben Tag/Nacht
        leiste.deleteLater()


# ============================================================================================== PDF-Bericht
class PdfBericht(unittest.TestCase):
    def setUp(self):
        from pyst import gui
        self.gui = gui
        self.w = gui.MainWindow()
        self.w.resize(1600, 1000)
        self.w.show()
        pump(0.1)
        db = self.w.db
        self.vid = db.save_vehicle({"hersteller": "Bericht", "modell": "RS 125"})
        self.sid = db.save_setup(self.vid, "Serie, HD 135", {"hauptduese": "135"})
        self.w._reload_vehicle_combo(self.vid, self.sid)
        self.tmp = tempfile.mkdtemp(prefix="pyst-pdf-")
        self.ids = []
        for f in XMLS[:3]:
            rid = db.import_file(os.path.join(DATA, f), vehicle_id=self.vid, setup_id=self.sid)
            db.update_run(rid, sichtbar=True)
            self.ids.append(rid)
        self.w._reload_runs()
        self.neu = []

    def tearDown(self):
        from conftest import close_window
        self.w.auto_mittel_setzen(False)
        for rid in self.ids + self.neu:                  # gemeinsame Datenbank der Tests
            self.w.db.delete_run(rid, delete_files=True)
        self.w.db.set_gesperrte_plaetze(self.vid, [])
        close_window(self.w, APP)
        theme.manager().setze(theme.TAG, speichern=False)
        shutil.rmtree(self.tmp, ignore_errors=True)
        gc.collect()
        APP.processEvents()

    def test_auto_mittelwert_und_referenz_im_bericht(self):
        w = self.w
        rows = w.pdf_rows()
        self.assertEqual(len(rows), 3)
        self.assertFalse(any(r["art"] == "auto" for r in rows))
        w.laufliste.auto.setChecked(True)                                     # Ø automatisch mitteln
        rows = w.pdf_rows()
        auto = rows[0]
        self.assertEqual(auto["art"], "auto")
        self.assertEqual(auto["name"].split("\n")[0], "Ø 3 Läufe · Serie HD 135 (automatisch)")
        erg = w._auto["erg"]
        self.assertIn(f"Pmax {erg.p_max:.1f} PS", auto["ergebnis"])
        self.assertIn(f"Mmax {erg.m_max:.1f} Nm", auto["ergebnis"])
        quellen = [w._run_label(w.db.run(i)).split("  ")[0] for i in w._auto["ids"]]
        for q in quellen:
            self.assertIn(q, auto["details"])
        self.assertIn("Band min–max", auto["details"])
        self.assertEqual(auto["farbe"], theme.laufarbe("@avg_color", theme.TAG))
        self.assertEqual(len(rows), 4)
        # gespeicherter Mittelwert als Referenz (gesperrt) kommt mit Band und Quellen dazu
        mw = w.auto_mittel_speichern()
        self.neu.append(mw)
        w.laeufe_hinzufuegen([mw], melden=False)
        rows = w.pdf_rows()
        ref = [r for r in rows if r["art"] == "mittelwert"]
        self.assertEqual(len(ref), 1)
        self.assertIn("Mittelwert (Referenz)", ref[0]["name"])
        self.assertIn("Platz gesperrt", ref[0]["name"])
        self.assertIn("Quellen:", ref[0]["details"])
        # Kopfzeile: Herkunft, Drehzahlbereich, Kurven, Achsgrenzen
        w.plot.set_limits({"n_lo": 4000})
        unter = bericht.messen_angaben(w)
        self.assertIn("Diagramm aus Messen", unter)
        self.assertIn("Leistung [PS]", unter)
        self.assertIn("unten: Lambda", unter)
        self.assertIn("Achsgrenzen fest: n ab 4.000 1/min", unter)
        path = os.path.join(self.tmp, "messen.pdf")
        self.assertEqual(w.on_pdf(path), path)
        self.assertGreater(os.path.getsize(path), 10000)

    def test_pdf_knopf_statusleiste_messen_und_auswertung(self):
        w = self.w
        path = os.path.join(self.tmp, "knopf.pdf")
        geoeffnet = []
        alt_save, alt_open = QtWidgets.QFileDialog.getSaveFileName, QtGui.QDesktopServices.openUrl
        QtWidgets.QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (path, "PDF (*.pdf)"))
        QtGui.QDesktopServices.openUrl = staticmethod(lambda url: geoeffnet.append(url.toLocalFile()) or True)
        try:
            w.show_page(w.seiten["messen"])
            w.statusleiste.pdf_knopf.click()                                  # Dateidialog umgangen
        finally:
            QtWidgets.QFileDialog.getSaveFileName, QtGui.QDesktopServices.openUrl = alt_save, alt_open
        self.assertTrue(os.path.exists(path))
        self.assertEqual(geoeffnet, [path])                                   # danach oeffnen wie bisher
        bild, titel, unter, rows, _h = bericht.daten(w, bericht.MESSEN)
        self.assertEqual(titel, w.vehicle_combo.currentText())
        self.assertFalse(bild.isNull())
        self.assertEqual({r["name"].split("\n")[0] for r in rows},
                         {w._run_label(w.db.run(i)).split("\n")[0] for i in self.ids})
        # Auswertung aktiv: deren Felder und Laeufe
        w.show_page(w.seiten["auswertung"])
        w.viewer.add_runs(self.ids[:2])
        pump(0.05)
        p2 = os.path.join(self.tmp, "auswertung.pdf")
        self.assertEqual(w.on_pdf_aktiv(p2), p2)
        self.assertTrue(os.path.exists(p2))
        _b, _t, unter, rows, _h = bericht.daten(w, bericht.AUSWERTUNG)
        self.assertIn("Diagramm aus der Auswertung", unter)
        self.assertIn("Feld 1:", unter)
        self.assertEqual(len(rows), 2)
        self.assertEqual(theme.manager().aktiv, theme.TAG)
        # Nachtmodus: Bild trotzdem auf weissem Papier, danach wieder Nacht
        theme.manager().setze(theme.NACHT, speichern=False)
        self.assertEqual(w.on_pdf_aktiv(os.path.join(self.tmp, "nacht.pdf")), os.path.join(self.tmp, "nacht.pdf"))
        self.assertEqual(theme.manager().aktiv, theme.NACHT)

    def test_viele_laeufe_naechste_seite(self):
        from pyst import report
        rows = [{"farbe": "#c62828", "name": f"Lauf {i}\nZeile 2\nZeile 3", "fahrzeug": "x", "ergebnis": "1\n2\n3",
                 "klima": "", "details": "a\nb\nc\nd"} for i in range(40)]
        path = os.path.join(self.tmp, "lang.pdf")
        report.export_pdf(path, self.w.plot.render_image(), "Test", rows, ["Fuss 1", "Fuss 2"], untertitel="u")
        with open(path, "rb") as fh:
            text = fh.read()
        self.assertGreaterEqual(text.count(b"/Type /Page") - text.count(b"/Type /Pages"), 2)


# ============================================================================================== Ordner-Import
def _pyst_ordner(ziel: str, mit_xml: bool = True) -> str:
    """PyST-Laufordner (lauf.json + roh.csv) aus dem Terminal-Mitschnitt, dazu lauf.xml wie beim Speichern."""
    run = storage.parse_terminal_log(LOG)
    run["meta"]["programm"] = "PyST 1.2.0"
    storage.write_imported_log(run, ziel)
    if mit_xml:
        shutil.copy2(os.path.join(DATA, XMLS[5]), os.path.join(ziel, "lauf.xml"))
    return ziel


class OrdnerImport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pyst-ordner-")
        self.db = Database(os.path.join(self.tmp, "daten", "pyst.db"))
        self.ordner = os.path.join(self.tmp, "alte_messungen")
        o = self.ordner
        os.makedirs(os.path.join(o, "2025", "mai", "tief"))
        os.makedirs(os.path.join(o, "2026"))
        os.makedirs(os.path.join(o, ".versteckt"))
        shutil.copy2(os.path.join(DATA, XMLS[0]), os.path.join(o, "2025", XMLS[0]))
        shutil.copy2(os.path.join(DATA, XMLS[1]), os.path.join(o, "2025", "mai", "tief", XMLS[1]))
        shutil.copy2(os.path.join(DATA, XMLS[0]), os.path.join(o, "2026", XMLS[0]))      # doppelt im Ordner
        shutil.copy2(os.path.join(DATA, XMLS[2]), os.path.join(o, "2026", XMLS[2]))      # schon in der DB
        shutil.copy2(os.path.join(DATA, XMLS[2]), os.path.join(o, "2026", "umbenannt.xml"))   # dto., anderer Name
        shutil.copy2(os.path.join(DATA, XMLS[1]), os.path.join(o, "2026", "kopie_b.xml"))     # doppelt, umbenannt
        shutil.copy2(os.path.join(DATA, XMLS[3]), os.path.join(o, ".versteckt", XMLS[3]))
        shutil.copy2(LOG, os.path.join(o, "2026", "mitschnitt.log"))
        _pyst_ordner(os.path.join(o, "2025", "mai", "260930_125310_31,6_9900"))
        with open(os.path.join(o, "2026", "notiz.txt"), "w") as fh:
            fh.write("keine Messung\n")
        with open(os.path.join(o, "2026", "kaputt.xml"), "w") as fh:
            fh.write("<LVData><Cluster></Cluster></LVData>")
        self.alt = self.db.import_file(os.path.join(DATA, XMLS[2]))

    def tearDown(self):
        self.db.con.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_kandidaten_rekursiv_ein_lauf_je_pyst_ordner(self):
        k = oi.kandidaten(self.ordner)
        rel = sorted((os.path.relpath(p, self.ordner), a) for p, a in k)
        self.assertEqual(rel, sorted([
            (os.path.join("2025", XMLS[0]), oi.ART_LABVIEW),
            (os.path.join("2025", "mai", "260930_125310_31,6_9900"), oi.ART_ORDNER),   # nicht lauf.xml extra
            (os.path.join("2025", "mai", "tief", XMLS[1]), oi.ART_LABVIEW),
            (os.path.join("2026", XMLS[0]), oi.ART_LABVIEW),
            (os.path.join("2026", XMLS[2]), oi.ART_LABVIEW),
            (os.path.join("2026", "kopie_b.xml"), oi.ART_LABVIEW),
            (os.path.join("2026", "umbenannt.xml"), oi.ART_LABVIEW),
            (os.path.join("2026", "kaputt.xml"), oi.ART_LABVIEW),
            (os.path.join("2026", "mitschnitt.log"), oi.ART_MITSCHNITT)]))           # notiz.txt nicht

    def test_suchen_vorschau_duplikate_und_import(self):
        funde = {os.path.relpath(f.pfad, self.ordner): f for f in oi.suchen(self.db, self.ordner)}
        a = funde[os.path.join("2025", XMLS[0])]
        self.assertTrue(a.vorgewaehlt)
        self.assertEqual(a.status(), "neu")
        self.assertEqual(a.datum[:10], "2026-05-26")
        self.assertTrue(a.erkannt.startswith("LabVIEW-XML (PST 3.x)"))
        kopie = funde[os.path.join("2026", XMLS[0])]
        self.assertTrue(kopie.duplikat)
        self.assertIn("doppelt im Ordner", kopie.status())
        vorhanden = funde[os.path.join("2026", XMLS[2])]
        self.assertEqual((vorhanden.vorhanden, vorhanden.status(), vorhanden.vorgewaehlt), (1, "schon vorhanden", False))
        umbenannt = funde[os.path.join("2026", "umbenannt.xml")]                 # gleicher Inhalt wie in der DB
        self.assertEqual((umbenannt.vorhanden, umbenannt.vorgewaehlt), (1, False))
        kopie_b = funde[os.path.join("2026", "kopie_b.xml")]                     # gleicher Inhalt wie tief/…
        self.assertIn("doppelt im Ordner", kopie_b.status())
        self.assertFalse(kopie_b.vorgewaehlt)
        kaputt = funde[os.path.join("2026", "kaputt.xml")]
        self.assertFalse(kaputt.importierbar)
        self.assertIn("nicht lesbar", kaputt.status())
        ordner = funde[os.path.join("2025", "mai", "260930_125310_31,6_9900")]
        self.assertEqual(ordner.erkannt, "PyST-Laufordner (PyST 1.2.0)")
        self.assertTrue(ordner.vorgewaehlt)
        log = funde[os.path.join("2026", "mitschnitt.log")]
        self.assertTrue(log.erkannt.startswith("Terminal-Mitschnitt"))
        # Import mit Zuordnung
        vid = self.db.save_vehicle({"hersteller": "Vespa", "modell": "PK 50"})
        sid = self.db.save_setup(vid, "Polini", {})
        wahl = [f for f in funde.values() if f.vorgewaehlt]
        self.assertEqual(len(wahl), 4)
        from pyst import physics
        b = oi.importieren(self.db, wahl, vid, sid, params=physics.DynoParams())
        self.assertEqual((len(b.importiert), b.fehler, b.abgebrochen), (4, [], False))
        for rid in b.ids:
            row = self.db.run(rid)
            self.assertEqual((row.vehicle_id, row.setup_id), (vid, sid))
            self.assertTrue(os.path.isdir(row.ordner))
        quellen = sorted(self.db.run(rid).quelle for rid in b.ids)
        self.assertEqual(quellen, ["LabVIEW", "LabVIEW", "PyST", "Terminal"])
        # nochmal suchen: alles schon da
        zweit = oi.suchen(self.db, self.ordner)
        self.assertFalse([f.pfad for f in zweit if f.vorgewaehlt])
        # Mitschnitt ohne Pruefstandsdaten wird uebersprungen, nicht falsch gerechnet
        b2 = oi.importieren(self.db, [log], vid, sid, params=None)
        self.assertEqual(b2.importiert, [])
        self.assertIn("Prüfstandsdaten", b2.uebersprungen[0][1])
        kurz, lang = oi.bericht_text(b, self.ordner)
        self.assertIn("Importiert: 4", kurz)
        self.assertIn("IMPORTIERT", lang)

    def test_pyst_paket_und_abbrechen(self):
        rid = self.db.import_file(os.path.join(DATA, XMLS[4]))
        row = self.db.run(rid)
        paket = os.path.join(self.ordner, "2026", "kunde.pyst")
        storage.export_pyst(paket, [{"ordner": row.ordner, "name": row.name, "datum": row.datum,
                                     "quelle": row.quelle, "art": row.art, "fahrzeug": {}, "setup": {},
                                     "parameter": row.params}])
        f = oi.pruefen(self.db, paket, oi.ART_PYST)
        self.assertEqual((f.laeufe, f.vorhanden, f.duplikat), (1, 1, True))       # schon in der DB
        self.db.delete_run(rid, delete_files=True)
        f = oi.pruefen(self.db, paket, oi.ART_PYST)
        self.assertEqual((f.vorhanden, f.vorgewaehlt), (0, True))
        b = oi.importieren(self.db, [f])
        self.assertEqual(len(b.importiert), 1)
        b = oi.importieren(self.db, [f])                                           # zweites Mal: nichts neu
        self.assertEqual(b.importiert, [])
        self.assertIn("schon vorhanden", b.uebersprungen[0][1])
        # Abbrechen
        funde = oi.suchen(self.db, self.ordner, lambda i, n, p: i < 2)
        self.assertEqual(len(funde), 2)
        b = oi.importieren(self.db, [f for f in oi.suchen(self.db, self.ordner) if f.vorgewaehlt],
                           params=None, fortschritt=lambda i, n, p: i < 1)
        self.assertTrue(b.abgebrochen)
        self.assertEqual(len(b.importiert), 1)


class OrdnerImportFenster(unittest.TestCase):
    """Ablauf aus dem Hauptfenster: Menue Datei, Knopf der Auswertung, Vorschau-Dialog, Zuordnung, Ergebnis."""

    def setUp(self):
        from pyst import gui
        self.w = gui.MainWindow()
        self.w.show()
        pump(0.05)
        self.tmp = tempfile.mkdtemp(prefix="pyst-ordner-ui-")
        os.makedirs(os.path.join(self.tmp, "a", "b"))
        shutil.copy2(os.path.join(DATA, XMLS[5]), os.path.join(self.tmp, "a", XMLS[5]))
        shutil.copy2(LOG, os.path.join(self.tmp, "a", "b", "mitschnitt.log"))
        _pyst_ordner(os.path.join(self.tmp, "a", "b", "lauf_1"), mit_xml=False)
        self.vid = self.w.db.save_vehicle({"hersteller": "Ordner", "modell": "Import"})
        self.sid = self.w.db.save_setup(self.vid, "Standard", {})
        self.w._reload_vehicle_combo(self.vid, self.sid)
        self.patches = []

    def _patch(self, obj, name, wert):
        self.patches.append((obj, name, getattr(obj, name)))
        setattr(obj, name, wert)

    def tearDown(self):
        from conftest import close_window
        for obj, name, alt in reversed(self.patches):
            setattr(obj, name, alt)
        for row in self.w.db.runs(vehicle_id=self.vid):
            self.w.db.delete_run(row.id, delete_files=True)
        close_window(self.w, APP)
        shutil.rmtree(self.tmp, ignore_errors=True)
        gc.collect()
        APP.processEvents()

    def test_menue_und_knopf(self):
        w = self.w
        datei = next(a.menu() for a in w.menuBar().actions() if a.text() == "Datei")
        self.assertIn("Ordner importieren (mit Unterordnern) …", [a.text() for a in datei.actions()])
        self.assertEqual([a.text() for a in w.viewer.import_btn.menu().actions()],
                         ["Dateien …", "Ordner mit Unterordnern …"])

    def test_ablauf_mit_vorschau_zuordnung_und_mitschnitt(self):
        w = self.w
        from pyst import share_ui
        gesehen = {}

        def exec_dialog(dlg):
            gesehen["zeilen"] = [dlg.liste.topLevelItem(i).text(0) for i in range(dlg.liste.topLevelItemCount())]
            gesehen["fahrzeug"] = dlg.fahrzeug.currentData()
            gesehen["setup"] = dlg.setup.currentData()
            gesehen["ok"] = dlg.b_ok.text()
            return QtWidgets.QDialog.Accepted

        params_gefragt = []
        self._patch(oi.OrdnerImportDialog, "exec", exec_dialog)
        self._patch(share_ui.ImportParamsDialog, "exec",
                    lambda dlg: params_gefragt.append(1) or QtWidgets.QDialog.Accepted)
        self._patch(oi, "ergebnis_zeigen", lambda parent, b, basis: gesehen.setdefault("bericht", b))
        b = w.on_import_ordner(self.tmp)
        self.assertEqual(sorted(gesehen["zeilen"]),
                         sorted([os.path.join("a", XMLS[5]), os.path.join("a", "b", "lauf_1") + os.sep,
                                 os.path.join("a", "b", "mitschnitt.log")]))
        self.assertEqual((gesehen["fahrzeug"], gesehen["setup"]), (self.vid, self.sid))   # Standard: aktuelles
        self.assertEqual(gesehen["ok"], "Importieren (3)")
        self.assertEqual(params_gefragt, [1])                                 # einmal fuer alle Mitschnitte
        self.assertIs(gesehen["bericht"], b)
        self.assertEqual(len(b.importiert), 3)
        ids = {r.id for r in w.db.runs(vehicle_id=self.vid)}
        self.assertEqual(ids, set(b.ids))
        t = w.run_tree
        in_liste = {t.topLevelItem(i).data(0, QtCore.Qt.UserRole) for i in range(t.topLevelItemCount())}
        self.assertTrue(in_liste & ids)                                       # Liste neu geladen
        # nochmal: alles Duplikat -> nichts gewaehlt, Knopf aus
        gesehen.clear()
        self._patch(oi.OrdnerImportDialog, "exec",
                    lambda dlg: gesehen.update(ok=dlg.b_ok.text(), an=dlg.b_ok.isEnabled()) or
                    QtWidgets.QDialog.Rejected)
        self.assertIsNone(w.on_import_ordner(self.tmp))
        self.assertEqual((gesehen["ok"], gesehen["an"]), ("Importieren (0)", False))


if __name__ == "__main__":
    unittest.main()
