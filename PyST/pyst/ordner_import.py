"""
Ordner mit Unterordnern importieren (Menue Datei › „Ordner importieren …“, Auswertung › Importieren › „Ordner mit
Unterordnern …“). Ergaenzt den Datei-Import (gui.open_files) fuer ganze Sammlungen alter Messungen.

Ablauf (ordner_importieren):
  1. suchen(): rekursiv alle unterstuetzten Messungen finden (kandidaten) und lesen (pruefen):
       LabVIEW-3.2.1-XML der alten Software (PST 3.x)        -> ART_LABVIEW
       PyST-Laufordner (lauf.json bzw. dauer.json, auch aeltere PyST-Versionen) -> ART_ORDNER, EIN Lauf je Ordner
           (lauf.xml, roh.csv … desselben Ordners zaehlen nicht extra, Unterordner werden nicht weiter durchsucht)
       .pyst-Pakete (ein oder mehrere Laeufe)                 -> ART_PYST
       Terminal-Mitschnitte (*.log, *.txt, *.csv mit Telegrammen) -> ART_MITSCHNITT
       Mittelwert-Laeufe (mittelwert.json) werden erkannt, aber nicht importiert.
     Jeder Fund bekommt Datum, „erkannt als“ und ob er schon in der Datenbank steht (schon_da: gleicher Name und
     Quelle, Datum hoechstens DUP_TOLERANZ_S auseinander, derselbe Ordner, oder eine Datei gleichen Inhalts) oder im
     Ordner doppelt vorkommt (Dateien: gleicher Inhalt, auch umbenannt; Laufordner: Name, Quelle, Datum).
  2. OrdnerImportDialog: Vorschau-Liste (Datei/Ordner, Datum, erkannt als, Status), Duplikate und Unlesbares
     abgewaehlt; Zuordnung Fahrzeug/Setup (Standard: das aktuelle).
  3. Terminal-Mitschnitte: einmal der Dialog mit den Pruefstandsdaten (share_ui.ImportParamsDialog) fuer alle.
  4. importieren(): mit db.import_file / db.import_pyst (dieselbe Logik wie der Datei-Import), Fortschritt mit
     Abbrechen; Ergebnisbericht importiert / uebersprungen / Fehler mit Grund (Bericht, ergebnis_zeigen).
"""
import datetime as _dt
import hashlib
import os
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

from PySide6 import QtCore, QtGui, QtWidgets

from . import storage, theme

ART_LABVIEW, ART_ORDNER, ART_PYST, ART_MITSCHNITT, ART_MITTELWERT = (
    "labview", "pyst_ordner", "pyst_datei", "mitschnitt", "mittelwert")
ART_TEXT = {ART_LABVIEW: "LabVIEW-XML (PST 3.x)", ART_ORDNER: "PyST-Laufordner", ART_PYST: ".pyst-Paket",
            ART_MITSCHNITT: "Terminal-Mitschnitt", ART_MITTELWERT: "Mittelwert-Lauf (PyST)"}
MITSCHNITT_ENDUNGEN = (".log", ".txt", ".csv")
LAUF_DATEIEN = ("lauf.json", "dauer.json")     # daran erkennt man einen PyST-Laufordner
DUP_TOLERANZ_S = 120                           # Datum in der Datenbank (Speichern) und in lauf.json weichen ab


@dataclass
class Fund:
    pfad: str
    art: str
    name: str = ""
    datum: str = ""                    # ISO wie in der Datenbank
    quelle: str = ""                   # wie db.runs.quelle (LabVIEW, PyST, Dauermessung, Terminal)
    erkannt: str = ""                  # Text „erkannt als …“
    laeufe: int = 1                    # .pyst: Zahl der Laeufe
    vorhanden: int = 0                 # davon schon in der Datenbank
    doppelt: str = ""                  # im Ordner schon einmal gefunden (Pfad des ersten)
    fehler: str = ""                   # nicht lesbar / nicht importierbar (Grund)
    inhalt: str = ""                   # datei_hash (nur Dateien)

    @property
    def duplikat(self) -> bool:
        return bool(self.doppelt) or (self.laeufe > 0 and self.vorhanden >= self.laeufe)

    @property
    def importierbar(self) -> bool:
        return not self.fehler

    @property
    def vorgewaehlt(self) -> bool:
        return self.importierbar and not self.duplikat

    def status(self) -> str:
        if self.fehler:
            return self.fehler
        if self.doppelt:
            return f"doppelt im Ordner (wie {os.path.basename(self.doppelt)})"
        if self.art == ART_PYST and self.laeufe > 1:
            if self.vorhanden >= self.laeufe:
                return f"{self.laeufe} Läufe, alle schon vorhanden"
            return f"{self.laeufe} Läufe" + (f", {self.vorhanden} schon vorhanden" if self.vorhanden else ", neu")
        return "schon vorhanden" if self.vorhanden else "neu"


@dataclass
class Bericht:
    importiert: List[Tuple[str, int]] = field(default_factory=list)      # (Pfad, Lauf-ID)
    uebersprungen: List[Tuple[str, str]] = field(default_factory=list)   # (Pfad, Grund)
    fehler: List[Tuple[str, str]] = field(default_factory=list)          # (Pfad, Grund)
    abgebrochen: bool = False

    @property
    def ids(self) -> List[int]:
        return [rid for _p, rid in self.importiert]


# ============================================================================================== suchen
def _iso(datum: str) -> str:
    from .db import Database
    return Database._iso(datum or "")


def _zeit(iso: str) -> Optional[_dt.datetime]:
    try:
        return _dt.datetime.fromisoformat((iso or "").strip()[:19])
    except ValueError:
        return None


def datum_text(iso: str) -> str:
    """„2026-05-26T16:18:02“ -> „26.05.2026 16:18“ (sonst unveraendert)."""
    t = _zeit(iso)
    return t.strftime("%d.%m.%Y %H:%M") if t else (iso or "–")


def kandidaten(ordner: str, abbrechen: Optional[Callable[[], bool]] = None) -> List[Tuple[str, str]]:
    """Rekursiv (Pfad, Art) aller unterstuetzten Messungen, ohne sie zu laden. Ein Ordner mit lauf.json/dauer.json
    ist EIN Lauf (seine Dateien und Unterordner zaehlen nicht extra); versteckte Ordner werden uebergangen."""
    out: List[Tuple[str, str]] = []
    for root, dirs, files in os.walk(ordner):
        if abbrechen is not None and abbrechen():
            break
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        namen = {f.lower() for f in files}
        if any(n in namen for n in LAUF_DATEIEN):
            out.append((root, ART_ORDNER))
            dirs[:] = []
            continue
        if "mittelwert.json" in namen:
            out.append((root, ART_MITTELWERT))
            dirs[:] = []
            continue
        for f in sorted(files):
            p = os.path.join(root, f)
            low = f.lower()
            if f.startswith("."):
                continue
            if low.endswith(storage.PYST_EXT):
                out.append((p, ART_PYST))
            elif low.endswith(".xml"):
                out.append((p, ART_LABVIEW))
            elif low.endswith(MITSCHNITT_ENDUNGEN) and storage.is_terminal_log(p):
                out.append((p, ART_MITSCHNITT))
    return out


_HASHES: dict = {}                             # (Pfad, Groesse, mtime) -> Pruefsumme


def datei_hash(pfad: str) -> str:
    """Pruefsumme einer Datei (gleicher Inhalt = dieselbe Messung, auch unter anderem Namen); "" = nicht lesbar.
    Gemerkt je Pfad, Groesse und Aenderungszeit."""
    try:
        st = os.stat(pfad)
        key = (pfad, st.st_size, st.st_mtime)
        if key in _HASHES:
            return _HASHES[key]
        h = hashlib.sha1()
        with open(pfad, "rb") as fh:
            for block in iter(lambda: fh.read(1 << 20), b""):
                h.update(block)
    except OSError:
        return ""
    _HASHES[key] = h.hexdigest()
    return _HASHES[key]


def schon_da(db, name: str, datum: str, quelle: str, ordner: str = "", inhalt: str = "",
             groesse: int = -1) -> Optional[int]:
    """Lauf-ID, wenn der Lauf schon in der Datenbank steht: derselbe Ordner, oder gleicher Name und Quelle und das
    Datum hoechstens DUP_TOLERANZ_S auseinander, oder (inhalt = datei_hash, groesse = Dateigroesse) eine Datei
    gleichen Inhalts mit derselben Quelle (umbenannte Kopie; erst Groesse, dann Pruefsumme vergleichen)."""
    if ordner:
        real = os.path.realpath(ordner)
        for r in db.con.execute("SELECT id, ordner FROM runs WHERE ordner IS NOT NULL"):
            if r["ordner"] and os.path.realpath(r["ordner"]) == real:
                return r["id"]
    t = _zeit(datum)
    for r in db.con.execute("SELECT id, datum FROM runs WHERE name=? AND quelle=?", (name, quelle)):
        if (r["datum"] or "") == datum:
            return r["id"]
        t2 = _zeit(r["datum"])
        if t is not None and t2 is not None and abs((t - t2).total_seconds()) <= DUP_TOLERANZ_S:
            return r["id"]
    if inhalt:
        for r in db.con.execute("SELECT id, ordner, datei FROM runs WHERE quelle=?", (quelle,)):
            p = os.path.join(r["ordner"] or "", r["datei"] or "")
            try:
                passt = r["datei"] and (groesse < 0 or os.path.getsize(p) == groesse)
            except OSError:
                passt = False
            if passt and datei_hash(p) == inhalt:
                return r["id"]
    return None


def _programm(meta: dict) -> str:
    prog = str((meta or {}).get("programm", "")).split(" – ")[0].strip()
    return prog


def pruefen(db, pfad: str, art: str) -> Fund:
    """Fund lesen: Name, Datum, Quelle, erkannt als, schon in der Datenbank. Fehler beim Lesen -> fehler."""
    f = Fund(pfad=pfad, art=art, erkannt=ART_TEXT.get(art, art))
    try:
        if art == ART_MITTELWERT:
            f.name = os.path.basename(os.path.normpath(pfad))
            f.fehler = "Mittelwert-Läufe werden nicht importiert (Quellläufe importieren und neu mitteln)"
            return f
        if art == ART_PYST:
            laeufe = storage.load_pyst(pfad)
            f.laeufe = len(laeufe)
            f.name = os.path.basename(pfad)
            daten = []
            for run in laeufe:
                e = run["pyst"]
                datum = _iso(e.get("datum") or run.get("date", ""))
                daten.append(datum)
                if schon_da(db, e.get("name", run["name"]), datum, e.get("quelle") or run["source"]) is not None:
                    f.vorhanden += 1
            f.datum = min(daten) if daten else ""
            f.quelle = "pyst"
            return f
        if art == ART_MITSCHNITT:
            run = storage.parse_terminal_log(pfad)
        else:
            run = storage.load_any(pfad)
        f.datum = _iso(run.get("date", ""))
        if art == ART_LABVIEW:
            f.name = os.path.splitext(os.path.basename(pfad))[0]
            f.quelle = "LabVIEW"
            titel = " ".join(x for x in (run.get("vehicle", ""), run.get("title", "")) if x)
            if titel:
                f.erkannt += f" · {titel}"
        elif art == ART_ORDNER:
            f.name = run["name"]
            dauer = run.get("kind") == "dauermessung"
            f.quelle = "Dauermessung" if dauer else run["source"]
            f.erkannt = "PyST-Dauermessung" if dauer else ART_TEXT[ART_ORDNER]
            prog = _programm(run.get("meta", {}))
            if prog:
                f.erkannt += f" ({prog})"
        else:
            f.name = run["name"]
            f.quelle = run["source"]
            f.erkannt += f" · {len(run['dt'])} Telegramme"
        groesse = -1
        if art != ART_ORDNER:
            f.inhalt = datei_hash(pfad)
            groesse = os.path.getsize(pfad)
        if schon_da(db, f.name, f.datum, f.quelle, pfad if art == ART_ORDNER else "", f.inhalt,
                    groesse) is not None:
            f.vorhanden = 1
    except Exception as exc:                          # unlesbar: in der Vorschau mit Grund, nicht gewaehlt
        f.fehler = f"nicht lesbar: {exc}".replace(pfad, os.path.basename(pfad))
    return f


def suchen(db, ordner: str, fortschritt: Optional[Callable[[int, int, str], bool]] = None) -> List[Fund]:
    """Alle Funde im Ordner (rekursiv) gelesen und geprueft. fortschritt(i, n, pfad) -> False = abbrechen.
    Derselbe Lauf mehrmals im Ordner: die weiteren als „doppelt“."""
    liste = kandidaten(ordner)
    funde: List[Fund] = []
    gesehen = {}
    for i, (pfad, art) in enumerate(liste):
        if fortschritt is not None and fortschritt(i, len(liste), pfad) is False:
            break
        f = pruefen(db, pfad, art)
        if not f.fehler:
            key = f.inhalt or (f.name, f.quelle, f.datum)      # Dateien: gleicher Inhalt, Ordner: Name/Datum
            if key in gesehen:
                f.doppelt = gesehen[key]
            else:
                gesehen[key] = pfad
        funde.append(f)
    return funde


# ============================================================================================== importieren
def importieren(db, funde: List[Fund], vehicle_id=None, setup_id=None, params=None,
                fortschritt: Optional[Callable[[int, int, str], bool]] = None) -> Bericht:
    """Gewaehlte Funde mit der Logik des Datei-Imports uebernehmen. params: Pruefstandsdaten fuer Mitschnitte
    (None = Mitschnitte werden uebersprungen). fortschritt(i, n, pfad) -> False = abbrechen."""
    b = Bericht()
    for i, f in enumerate(funde):
        if fortschritt is not None and fortschritt(i, len(funde), f.pfad) is False:
            b.abgebrochen = True
            b.uebersprungen += [(g.pfad, "abgebrochen") for g in funde[i:]]
            break
        if f.fehler:
            b.uebersprungen.append((f.pfad, f.fehler))
            continue
        if f.art == ART_MITSCHNITT and params is None:
            b.uebersprungen.append((f.pfad, "Terminal-Mitschnitt ohne Prüfstandsdaten (Dialog abgebrochen)"))
            continue
        try:
            if f.art == ART_PYST:
                vorher = {r["id"] for r in db.con.execute("SELECT id FROM runs")}
                ids = db.import_pyst(f.pfad, vehicle_id=vehicle_id, setup_id=setup_id)
                neu = [rid for rid in ids if rid not in vorher]
                b.importiert += [(f.pfad, rid) for rid in neu]
                if len(neu) < len(ids):
                    b.uebersprungen.append((f.pfad, f"{len(ids) - len(neu)} Lauf/Läufe schon vorhanden"))
            else:
                rid = db.import_file(f.pfad, vehicle_id=vehicle_id, setup_id=setup_id,
                                     params=params if f.art == ART_MITSCHNITT else None)
                b.importiert.append((f.pfad, rid))
        except Exception as exc:
            b.fehler.append((f.pfad, str(exc).replace(f.pfad, os.path.basename(f.pfad)) or type(exc).__name__))
    return b


def bericht_text(b: Bericht, basis: str = "") -> Tuple[str, str]:
    """(Kurztext, Einzelheiten) fuer den Ergebnisbericht."""
    rel = lambda p: os.path.relpath(p, basis) if basis and p.startswith(basis) else p
    kurz = f"Importiert: {len(b.importiert)} Lauf/Läufe\nÜbersprungen: {len(b.uebersprungen)}\nFehler: {len(b.fehler)}"
    if b.abgebrochen:
        kurz += "\n\nDer Import wurde abgebrochen."
    teile = []
    if b.importiert:
        teile.append("IMPORTIERT\n" + "\n".join(f"  {rel(p)}" for p, _r in b.importiert))
    if b.uebersprungen:
        teile.append("ÜBERSPRUNGEN\n" + "\n".join(f"  {rel(p)}: {g}" for p, g in b.uebersprungen))
    if b.fehler:
        teile.append("FEHLER\n" + "\n".join(f"  {rel(p)}: {g}" for p, g in b.fehler))
    return kurz, "\n\n".join(teile)


# ============================================================================================== Oberflaeche
class OrdnerImportDialog(QtWidgets.QDialog):
    """Vorschau vor dem Import: je Fund Haken, Datei/Ordner (relativ zum gewaehlten Ordner), Datum, erkannt als,
    Status. Neue Laeufe sind gewaehlt, Duplikate und Unlesbares nicht (Unlesbares laesst sich nicht waehlen).
    Unten die Zuordnung Fahrzeug/Setup (Standard: das aktuelle)."""
    SPALTEN = ["Datei / Ordner", "Datum", "Erkannt als", "Status"]

    def __init__(self, funde: List[Fund], basis: str, db, vid=None, sid=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Ordner importieren")
        self.resize(1000, 600)
        self.funde, self.basis, self.db = list(funde), basis, db
        lay = QtWidgets.QVBoxLayout(self)
        neu = sum(f.vorgewaehlt for f in self.funde)
        dup = sum(f.importierbar and f.duplikat for f in self.funde)
        kaputt = sum(not f.importierbar for f in self.funde)
        self.kopf = QtWidgets.QLabel(
            f"<b>{len(self.funde)} Messungen gefunden</b> in {basis} (mit Unterordnern):  {neu} neu"
            + (f",  {dup} schon vorhanden (abgewählt)" if dup else "")
            + (f",  {kaputt} nicht importierbar" if kaputt else ""))
        self.kopf.setWordWrap(True)
        lay.addWidget(self.kopf)
        t = QtWidgets.QTreeWidget()
        t.setHeaderLabels(self.SPALTEN)
        t.setRootIsDecorated(False)
        t.setUniformRowHeights(True)
        t.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        for f in self.funde:
            rel = os.path.relpath(f.pfad, basis) if f.pfad.startswith(basis) else f.pfad
            if f.art in (ART_ORDNER, ART_MITTELWERT):
                rel = (rel if rel != "." else os.path.basename(f.pfad)) + os.sep
            it = QtWidgets.QTreeWidgetItem([rel, datum_text(f.datum) if f.datum else "–", f.erkannt, f.status()])
            it.setToolTip(0, f.pfad)
            it.setToolTip(2, f.erkannt)
            it.setToolTip(3, f.status())
            flags = QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable
            if f.importierbar:
                flags |= QtCore.Qt.ItemIsUserCheckable
                it.setCheckState(0, QtCore.Qt.Checked if f.vorgewaehlt else QtCore.Qt.Unchecked)
            it.setFlags(flags)
            if not f.vorgewaehlt:                        # Duplikat/unlesbar: Nebenschrift
                for c in range(len(self.SPALTEN)):
                    it.setForeground(c, QtGui.QBrush(QtGui.QColor(theme.farbe("dim"))))
            t.addTopLevelItem(it)
        for c, w in enumerate((420, 130, 260)):
            t.setColumnWidth(c, w)
        t.itemChanged.connect(lambda *_a: self._zaehlen())
        self.liste = t
        lay.addWidget(t, 1)
        knoepfe = QtWidgets.QHBoxLayout()
        self.b_neue = QtWidgets.QPushButton("Nur neue wählen")
        self.b_alle = QtWidgets.QPushButton("Alle wählen")
        self.b_keine = QtWidgets.QPushButton("Keine")
        self.b_neue.clicked.connect(lambda: self._waehlen(lambda f: f.vorgewaehlt))
        self.b_alle.clicked.connect(lambda: self._waehlen(lambda f: True))
        self.b_keine.clicked.connect(lambda: self._waehlen(lambda f: False))
        for b in (self.b_neue, self.b_alle, self.b_keine):
            knoepfe.addWidget(b)
        knoepfe.addStretch(1)
        lay.addLayout(knoepfe)

        zuordnung = QtWidgets.QHBoxLayout()
        zuordnung.addWidget(QtWidgets.QLabel("Zuordnen zu Fahrzeug"))
        self.fahrzeug = QtWidgets.QComboBox()
        self.fahrzeug.addItem("ohne Fahrzeug", None)
        for r in db.vehicles():
            self.fahrzeug.addItem(r["name"], r["id"])
        self.fahrzeug.setMinimumWidth(240)
        zuordnung.addWidget(self.fahrzeug)
        zuordnung.addWidget(QtWidgets.QLabel("Setup"))
        self.setup = QtWidgets.QComboBox()
        self.setup.setMinimumWidth(240)
        zuordnung.addWidget(self.setup)
        zuordnung.addStretch(1)
        lay.addLayout(zuordnung)
        hinweis = QtWidgets.QLabel(".pyst-Pakete bringen ihr Fahrzeug und Setup mit (sonst gilt die Auswahl). "
                                   "Terminal-Mitschnitte fragen danach einmal nach den Prüfstandsdaten. "
                                   "Mittelwert-Läufe werden nicht übernommen.")
        hinweis.setWordWrap(True)
        theme.rolle(hinweis, "neben")
        lay.addWidget(hinweis)
        self.fahrzeug.currentIndexChanged.connect(lambda _i: self._setups())
        i = self.fahrzeug.findData(vid) if vid else 0
        self.fahrzeug.setCurrentIndex(max(0, i))
        self._setups(sid)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        self.b_ok = bb.button(QtWidgets.QDialogButtonBox.Ok)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._zaehlen()

    def _setups(self, sid=None):
        self.setup.clear()
        self.setup.addItem("ohne Setup", None)
        vid = self.fahrzeug.currentData()
        if vid:
            for r in self.db.setups(vid):
                self.setup.addItem(r["name"] or "Setup", r["id"])
        i = self.setup.findData(sid) if sid else 0
        self.setup.setCurrentIndex(max(0, i))

    def _items(self):
        return [self.liste.topLevelItem(i) for i in range(self.liste.topLevelItemCount())]

    def _waehlen(self, regel: Callable[[Fund], bool]):
        self.liste.blockSignals(True)
        for it, f in zip(self._items(), self.funde):
            if f.importierbar:
                it.setCheckState(0, QtCore.Qt.Checked if regel(f) else QtCore.Qt.Unchecked)
        self.liste.blockSignals(False)
        self._zaehlen()

    def _zaehlen(self):
        n = len(self.ausgewaehlt())
        self.b_ok.setText(f"Importieren ({n})")
        self.b_ok.setEnabled(n > 0)

    def ausgewaehlt(self) -> List[Fund]:
        return [f for it, f in zip(self._items(), self.funde)
                if f.importierbar and it.checkState(0) == QtCore.Qt.Checked]

    def zuordnung(self) -> Tuple[Optional[int], Optional[int]]:
        return self.fahrzeug.currentData(), self.setup.currentData()


class _Fortschritt:
    """QProgressDialog mit Abbrechen als Rueckruf fortschritt(i, n, pfad) -> False = abgebrochen."""

    def __init__(self, parent, titel: str, text: str):
        self.dlg = QtWidgets.QProgressDialog(text, "Abbrechen", 0, 1, parent)
        self.dlg.setWindowTitle(titel)
        self.dlg.setWindowModality(QtCore.Qt.WindowModal)
        self.dlg.setMinimumDuration(300)
        self.dlg.setMinimumWidth(520)
        self.text = text

    def __call__(self, i: int, n: int, pfad: str) -> bool:
        self.dlg.setMaximum(max(1, n))
        self.dlg.setValue(i)
        self.dlg.setLabelText(f"{self.text}\n{i + 1} von {n}: {os.path.basename(os.path.normpath(pfad))}")
        QtWidgets.QApplication.processEvents()
        return not self.dlg.wasCanceled()

    def fertig(self):
        self.dlg.setValue(self.dlg.maximum())
        self.dlg.close()


def ergebnis_zeigen(parent, b: Bericht, basis: str):
    kurz, lang = bericht_text(b, basis)
    box = QtWidgets.QMessageBox(QtWidgets.QMessageBox.Warning if b.fehler else QtWidgets.QMessageBox.Information,
                                "Ordner importieren", kurz, QtWidgets.QMessageBox.Ok, parent)
    if b.importiert:
        box.setInformativeText("Die Läufe stehen in der Liste Läufe (Messen) und in der Auswertung unter "
                               "„Läufe & Kanäle“.")
    if lang:
        box.setDetailedText(lang)
    box.exec()


def ordner_importieren(win, ordner: str = "") -> Optional[Bericht]:
    """Ganzer Ablauf aus dem Hauptfenster (gui.on_import_ordner). -> Bericht bzw. None (abgebrochen/nichts)."""
    from .share_ui import ImportParamsDialog
    if not ordner:
        ordner = QtWidgets.QFileDialog.getExistingDirectory(win, "Ordner mit alten Messungen (mit Unterordnern)",
                                                            os.path.expanduser("~"))
    if not ordner:
        return None
    ordner = os.path.abspath(ordner)
    fs = _Fortschritt(win, "Ordner importieren", "Messungen suchen und lesen …")
    funde = suchen(win.db, ordner, fs)
    abgebrochen = fs.dlg.wasCanceled()
    fs.fertig()
    if abgebrochen:
        return None
    if not funde:
        QtWidgets.QMessageBox.information(win, "Ordner importieren",
                                          f"In {ordner} (mit Unterordnern) wurden keine unterstützten Messungen "
                                          "gefunden.\n\nUnterstützt: LabVIEW-XML (PST 3.x), PyST-Laufordner "
                                          "(lauf.json), .pyst-Dateien, Terminal-Mitschnitte.")
        return None
    dlg = OrdnerImportDialog(funde, ordner, win.db, win.vehicle_combo.currentData(), win.setup_combo.currentData(),
                             win)
    if dlg.exec() != QtWidgets.QDialog.Accepted:
        return None
    wahl = dlg.ausgewaehlt()
    vid, sid = dlg.zuordnung()
    logs = [f.pfad for f in wahl if f.art == ART_MITSCHNITT]
    params = None
    if logs:
        pd = ImportParamsDialog(logs, win.page_settings.params(), win)
        if pd.exec() == QtWidgets.QDialog.Accepted:
            params = pd.params()
    fs = _Fortschritt(win, "Ordner importieren", "Läufe übernehmen …")
    b = importieren(win.db, wahl, vid, sid, params, fs)
    fs.fertig()
    nicht = [f for f in funde if f not in wahl]
    b.uebersprungen += [(f.pfad, f.fehler or ("schon vorhanden" if f.duplikat else "nicht gewählt")) for f in nicht]
    win._reload_runs()
    win.statusBar().showMessage(f"Ordner importiert: {len(b.importiert)} Lauf/Läufe übernommen", 8000)
    ergebnis_zeigen(win, b, ordner)
    return b
