"""
Eingabemasken fuer Fahrzeug- und Setup-Daten (gemeinsam genutzt vom Reiter "Fahrzeuge & Setups"
und von der Auswertung) und ein Bearbeiten-Dialog.
"""
from typing import Dict, Optional

from PySide6 import QtCore, QtWidgets

from .db import SETUP_FIELDS, VEHICLE_FIELDS, Database


class FieldForm(QtWidgets.QWidget):
    """Formular aus einer Feldliste [(schluessel, beschriftung, typ)], typ = "text" | "num" | [Auswahl]."""

    def __init__(self, fields, parent=None):
        super().__init__(parent)
        form = QtWidgets.QFormLayout(self)
        form.setContentsMargins(0, 0, 0, 0)
        self.widgets: Dict[str, QtWidgets.QWidget] = {}
        for key, label, typ in fields:
            if isinstance(typ, list):
                w = QtWidgets.QComboBox()
                w.setEditable(True)
                w.addItems([""] + typ)
            else:
                w = QtWidgets.QLineEdit()
                if typ == "num":
                    w.setPlaceholderText("Zahl")
            form.addRow(label, w)
            self.widgets[key] = w

    def values(self) -> Dict[str, str]:
        return {k: (w.currentText() if isinstance(w, QtWidgets.QComboBox) else w.text())
                for k, w in self.widgets.items()}

    def set_values(self, d: Optional[dict]):
        d = d or {}
        for k, w in self.widgets.items():
            v = str(d.get(k, "") or "")
            if isinstance(w, QtWidgets.QComboBox):
                w.setEditText(v)
            else:
                w.setText(v)

    def has_content(self) -> bool:
        return any(self.values().values())


class SetupForm(QtWidgets.QScrollArea):
    """Alle Setup-Gruppen in zwei Spalten, scrollbar."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        inner = QtWidgets.QWidget()
        grid = QtWidgets.QGridLayout(inner)
        self.forms = []
        for i, (group, fields) in enumerate(SETUP_FIELDS):
            gb = QtWidgets.QGroupBox(group)
            lay = QtWidgets.QVBoxLayout(gb)
            f = FieldForm(fields)
            lay.addWidget(f)
            grid.addWidget(gb, i // 2, i % 2)
            self.forms.append(f)
        self.setWidget(inner)

    def values(self) -> Dict[str, str]:
        out = {}
        for f in self.forms:
            out.update(f.values())
        return out

    def set_values(self, d: Optional[dict]):
        for f in self.forms:
            f.set_values(d)

    def has_content(self) -> bool:
        return any(f.has_content() for f in self.forms)


class EditDialog(QtWidgets.QDialog):
    """Fahrzeug und (optional) Setup eines Laufs bearbeiten."""

    def __init__(self, db: Database, vehicle_id: Optional[int], setup_id: Optional[int], parent=None,
                 title: str = "Fahrzeug / Setup bearbeiten"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(1000, 760)
        self.db = db
        self.vid, self.sid = vehicle_id, setup_id
        lay = QtWidgets.QVBoxLayout(self)

        vbox = QtWidgets.QGroupBox("Fahrzeug")
        vl = QtWidgets.QVBoxLayout(vbox)
        self.vform = FieldForm(VEHICLE_FIELDS[0][1])
        self.vnotiz = QtWidgets.QLineEdit()
        vl.addWidget(self.vform)
        hn = QtWidgets.QFormLayout()
        hn.addRow("Notiz", self.vnotiz)
        vl.addLayout(hn)
        lay.addWidget(vbox)

        sbox = QtWidgets.QGroupBox("Setup")
        sl = QtWidgets.QVBoxLayout(sbox)
        top = QtWidgets.QFormLayout()
        self.sname = QtWidgets.QLineEdit()
        self.snotiz = QtWidgets.QLineEdit()
        top.addRow("Name", self.sname)
        top.addRow("Notiz", self.snotiz)
        sl.addLayout(top)
        self.sform = SetupForm()
        sl.addWidget(self.sform, 1)
        lay.addWidget(sbox, 1)

        btns = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel)
        btns.button(QtWidgets.QDialogButtonBox.Save).setText("Speichern")
        btns.button(QtWidgets.QDialogButtonBox.Cancel).setText("Abbrechen")
        btns.accepted.connect(self._save)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

        v = db.vehicle(vehicle_id) if vehicle_id else None
        self.vform.set_values(v)
        self.vnotiz.setText((v or {}).get("notiz", ""))
        s = db.setup(setup_id) if setup_id else None
        self.sname.setText((s or {}).get("name", ""))
        self.snotiz.setText((s or {}).get("notiz", ""))
        self.sform.set_values(s)

    def _save(self):
        if self.vid or self.vform.has_content():
            vdata = self.vform.values()
            vdata["notiz"] = self.vnotiz.text()
            self.vid = self.db.save_vehicle(vdata, self.vid)
        if self.vid and (self.sid or self.sname.text() or self.sform.has_content()):
            self.sid = self.db.save_setup(self.vid, self.sname.text() or "Setup", self.sform.values(),
                                          self.snotiz.text(), self.sid)
        self.accept()
