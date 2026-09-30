"""
Anzeige-Bausteine: einzelne Anzeige (Gauge) und eine Anzeigenleiste mit Auswahlmenue (GaugeBar).
Genutzt im Reiter "Messen" (live + Cursor) und in der Auswertung (Cursor).
"""
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from PySide6 import QtCore, QtWidgets

CURSOR_BG = "#fff6cc"          # Hintergrund, solange Werte vom Cursor statt live angezeigt werden


class Gauge(QtWidgets.QFrame):
    def __init__(self, title: str, unit: str):
        super().__init__()
        self.setObjectName("gauge")
        self.setFrameShape(QtWidgets.QFrame.StyledPanel)
        self.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)   # darf schmaler werden
        self.setMinimumWidth(70)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(8, 2, 8, 2)
        lay.setSpacing(0)
        self.title = QtWidgets.QLabel(f"{title} [{unit}]" if unit else title)
        self.title.setStyleSheet("color: gray; font-size: 11px;")
        self.value = QtWidgets.QLabel("–")
        self.value.setStyleSheet("font-size: 20px; font-weight: 600;")
        self.value.setAlignment(QtCore.Qt.AlignRight)
        lay.addWidget(self.title)
        lay.addWidget(self.value)

    def set(self, text: str):
        self.value.setText(text)

    def set_title(self, title: str):
        self.title.setText(title)

    def set_highlight(self, on: bool):
        self.setStyleSheet(f"QFrame#gauge {{ background: {CURSOR_BG}; }}" if on else "")


class GaugeBar(QtWidgets.QWidget):
    """Leiste aus Anzeigen. defs: [(schluessel, titel, einheit)]. hidden: ausgeblendete Schluessel;
    on_hidden(set) wird bei Aenderung aufgerufen (zum Speichern)."""

    def __init__(self, defs: Iterable[Tuple[str, str, str]], hidden: Iterable[str] = (),
                 on_hidden: Optional[Callable[[set], None]] = None, prefix: Optional[QtWidgets.QWidget] = None):
        super().__init__()
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.hidden = set(hidden)
        self.on_hidden = on_hidden
        self.gauges: Dict[str, Gauge] = {}
        self.names: Dict[str, str] = {}
        if prefix is not None:
            lay.addWidget(prefix)
        for key, title, unit in defs:
            g = Gauge(title, unit)
            g.setVisible(key not in self.hidden)
            self.gauges[key] = g
            self.names[key] = title
            lay.addWidget(g)
        btn = QtWidgets.QToolButton()
        btn.setText("Anzeigen …")
        btn.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        menu = QtWidgets.QMenu(btn)
        for key, title, _unit in defs:
            act = menu.addAction(title)
            act.setCheckable(True)
            act.setChecked(key not in self.hidden)
            act.toggled.connect(lambda on, k=key: self._toggle(k, on))
        btn.setMenu(menu)
        lay.addWidget(btn)

    def _toggle(self, key: str, on: bool):
        self.gauges[key].setVisible(on)
        (self.hidden.discard if on else self.hidden.add)(key)
        if self.on_hidden:
            self.on_hidden(set(self.hidden))

    def set(self, key: str, text: str):
        if key in self.gauges:
            self.gauges[key].set(text)

    def set_title(self, key: str, text: str):
        if key in self.gauges:
            self.gauges[key].set_title(text)

    def set_highlight(self, on: bool):
        for g in self.gauges.values():
            g.set_highlight(on)

    def keys(self) -> List[str]:
        return list(self.gauges)
