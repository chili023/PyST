"""
Dialoge zum Weitergeben von Laeufen an Kunden und zum Import von Rohmitschnitten.

- ExportDialog: welche Laeufe (markierte, sichtbare, Fahrzeug, Tag) und wie (.pyst, Ordner mit .pyst je Lauf,
  HTML-Ansicht ohne PyST).
- ImportParamsDialog: Mitschnitte aus Terminalprogrammen enthalten keine Pruefstandsdaten -> abfragen.
"""
import os
from typing import Dict, List

from PySide6 import QtWidgets

from .physics import DynoParams

FORMAT_PYST, FORMAT_FOLDER, FORMAT_HTML = 0, 1, 2


class ExportDialog(QtWidgets.QDialog):
    def __init__(self, n_marked: int, n_visible: int, vehicle: str, n_vehicle: int, days: Dict[str, int],
                 parent=None, html: bool = False):
        super().__init__(parent)
        self.setWindowTitle("Läufe weitergeben")
        lay = QtWidgets.QVBoxLayout(self)
        g = QtWidgets.QGroupBox("Welche Läufe?")
        gl = QtWidgets.QVBoxLayout(g)
        self.r_marked = QtWidgets.QRadioButton(f"in der Liste markierte ({n_marked})")
        self.r_visible = QtWidgets.QRadioButton(f"angehakte / sichtbare ({n_visible})")
        self.r_vehicle = QtWidgets.QRadioButton(f"alle des Fahrzeugs „{vehicle}“ ({n_vehicle})" if vehicle
                                                else "alle des Fahrzeugs (kein Fahrzeug gewählt)")
        self.r_day = QtWidgets.QRadioButton("alle vom Tag")
        self.day = QtWidgets.QComboBox()
        for d, n in days.items():
            self.day.addItem(f"{d[8:10]}.{d[5:7]}.{d[:4]} ({n})", d)
        self.r_marked.setEnabled(n_marked > 0)
        self.r_visible.setEnabled(n_visible > 0)
        self.r_vehicle.setEnabled(bool(vehicle) and n_vehicle > 0)
        self.r_day.setEnabled(bool(days))
        (self.r_marked if n_marked else self.r_visible if n_visible else self.r_day).setChecked(True)
        for r in (self.r_marked, self.r_visible, self.r_vehicle):
            gl.addWidget(r)
        h = QtWidgets.QHBoxLayout()
        h.addWidget(self.r_day)
        h.addWidget(self.day, 1)
        gl.addLayout(h)
        lay.addWidget(g)
        g2 = QtWidgets.QGroupBox("Format")
        fl = QtWidgets.QVBoxLayout(g2)
        self.f_pyst = QtWidgets.QRadioButton("eine .pyst-Datei (öffnet in PyST, alle Messdaten, Fahrzeug/Setup)")
        self.f_folder = QtWidgets.QRadioButton("Ordner mit je einer .pyst-Datei pro Lauf")
        self.f_html = QtWidgets.QRadioButton("HTML-Ansicht – eine Datei, öffnet in jedem Browser ohne PyST")
        (self.f_html if html else self.f_pyst).setChecked(True)
        for r in (self.f_pyst, self.f_folder, self.f_html):
            fl.addWidget(r)
        lay.addWidget(g2)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def scope(self) -> str:
        if self.r_marked.isChecked():
            return "markiert"
        if self.r_visible.isChecked():
            return "sichtbar"
        if self.r_vehicle.isChecked():
            return "fahrzeug"
        return "tag"

    def fmt(self) -> int:
        return FORMAT_PYST if self.f_pyst.isChecked() else FORMAT_FOLDER if self.f_folder.isChecked() else FORMAT_HTML


IMPORT_FIELDS = [("inkr", "Rollengeber [Inkr/U]", 1, 10000, 0), ("roll_circ", "Rollenumfang [m]", 0.01, 20, 4),
                 ("inertia", "Rollenträgheit J [kgm²]", 0.01, 1000, 3), ("imp", "Zündung [Imp/U]", 0.25, 16, 2),
                 ("ratio", "Übersetzung nKW/nRolle", 0.01, 100, 3)]


class ImportParamsDialog(QtWidgets.QDialog):
    """Pruefstandsdaten fuer Rohmitschnitte (stehen nicht im Mitschnitt)."""

    def __init__(self, files: List[str], current: DynoParams, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Import Rohmitschnitt")
        lay = QtWidgets.QVBoxLayout(self)
        names = ", ".join(os.path.basename(f) for f in files[:3]) + (" …" if len(files) > 3 else "")
        lab = QtWidgets.QLabel(f"<b>{names}</b><br>Ein Terminal-Mitschnitt enthält nur die Telegramme (und ggf. "
                               "Klima und Firmware-Version), aber keine Prüfstandsdaten. Bitte die Daten des "
                               "Prüfstands angeben, auf dem gemessen wurde (vorbelegt: aktuelle Einstellungen). "
                               "Später änderbar mit „Mit aktuellen Einstellungen neu rechnen“.")
        lab.setWordWrap(True)
        lay.addWidget(lab)
        form = QtWidgets.QFormLayout()
        self.fields = {}
        for key, label, lo, hi, dec in IMPORT_FIELDS:
            sp = QtWidgets.QDoubleSpinBox()
            sp.setRange(lo, hi)
            sp.setDecimals(dec)
            sp.setValue(float(getattr(current, key)))
            form.addRow(label, sp)
            self.fields[key] = sp
        lay.addLayout(form)
        self.base = current
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.button(QtWidgets.QDialogButtonBox.Ok).setText("Importieren")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def params(self) -> DynoParams:
        p = DynoParams(**self.base.__dict__)
        for k, sp in self.fields.items():
            setattr(p, k, sp.value())
        return p
