"""Tabelle Einstellung/Wert/Bedeutung, gemeinsam fuer Geraete- und Zangen-Einstellungen. Lesen und Schreiben laufen im
Thread (bg.wait_in_thread): die Zange kann ausser Reichweite sein, dann dauert jede Antwort Sekunden."""
from typing import Callable, Dict, List, Sequence, Tuple

from PySide6 import QtCore, QtWidgets

from .bg import wait_in_thread


class KvDialog(QtWidgets.QDialog):
    def __init__(self, link, title: str, hint: str, parent=None):
        super().__init__(parent)
        self.link = link
        self.setWindowTitle(title)
        lay = QtWidgets.QVBoxLayout(self)
        lay.addWidget(QtWidgets.QLabel(hint))
        self.table = QtWidgets.QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Einstellung", "Wert", "Bedeutung"])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.Stretch)
        lay.addWidget(self.table, 1)
        self.msg = QtWidgets.QLabel("")
        self.msg.setWordWrap(True)
        lay.addWidget(self.msg)
        self.buttons = QtWidgets.QHBoxLayout()
        lay.addLayout(self.buttons)
        self.b_reload = QtWidgets.QPushButton("Neu lesen")
        self.b_apply = QtWidgets.QPushButton("Übernehmen")
        self.b_close = QtWidgets.QPushButton("Schließen")
        self.b_reload.clicked.connect(self.reload)
        self.b_apply.clicked.connect(self.apply)
        self.b_close.clicked.connect(self.accept)
        self.values: Dict[str, str] = {}

    def layout_buttons(self, *extra: QtWidgets.QPushButton):
        for b in (self.b_reload, self.b_apply, *extra):
            self.buttons.addWidget(b)
        self.buttons.addStretch(1)
        self.buttons.addWidget(self.b_close)

    def reload(self):
        raise NotImplementedError

    def wait(self, fn: Callable):
        """Arbeit im Thread, Sanduhr und gesperrte Knoepfe solange."""
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        self.setEnabled(False)
        try:
            return wait_in_thread(fn)
        finally:
            self.setEnabled(True)
            QtWidgets.QApplication.restoreOverrideCursor()

    def fill(self, rows: Sequence[Tuple[str, str, str]]):
        """rows: (Schluessel, Wert, Bedeutung)"""
        self.table.setRowCount(len(rows))
        for r, (k, v, help_) in enumerate(rows):
            a = QtWidgets.QTableWidgetItem(k)
            a.setFlags(a.flags() & ~QtCore.Qt.ItemIsEditable)
            c = QtWidgets.QTableWidgetItem(help_)
            c.setFlags(c.flags() & ~QtCore.Qt.ItemIsEditable)
            self.table.setItem(r, 0, a)
            self.table.setItem(r, 1, QtWidgets.QTableWidgetItem(v))
            self.table.setItem(r, 2, c)
        self.table.resizeColumnToContents(0)

    def changes(self, skip_empty: bool) -> List[Tuple[str, str]]:
        out = []
        for r in range(self.table.rowCount()):
            k = self.table.item(r, 0).text()
            v = self.table.item(r, 1).text().strip()
            if v != self.values.get(k) and (v or not skip_empty):
                out.append((k, v))
        return out

    def send(self, changes: List[Tuple[str, str]], setter: Callable[[str, str], List[str]], done_text: str) -> bool:
        """Aenderungen im Thread uebertragen und das Ergebnis anzeigen."""
        results = self.wait(lambda: [(k, v, setter(k, v)) for k, v in changes])
        errors, n = [], 0
        for k, v, lines in results:
            if self.link.ok(lines):
                self.values[k] = v
                n += 1
            else:
                errors.append(f"{k}: {lines[-1] if lines else 'keine Antwort'}")
        self.msg.setText(f"{n} Einstellung(en) {done_text}." + ("\nFehler: " + "; ".join(errors) if errors else ""))
        return not errors
