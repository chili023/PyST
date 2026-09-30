"""
Uebersetzung einmessen: Motordrehzahl / Rollendrehzahl bei konstanter Drehzahl.

Quelle der Motordrehzahl: Zuendabnehmer des Messboards oder rusEFI (RPMValue). Ist das Steuergeraet
verbunden, werden beide Quellen gleichzeitig gemessen – so faellt ein doppelt zaehlender Zuendabnehmer
sofort auf. Das Diagramm zeigt die Uebersetzung ueber der Messzeit; die rote Linie (getrimmter
Mittelwert der gewaehlten Quelle) laesst sich ziehen, per Klick setzen oder als Zahl eingeben.
Uebernommen wird der Wert der Linie.
"""
import time
from typing import Callable, List, Optional

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtWidgets

from . import physics

ECU_RPM = "RPMValue"
SOURCES = (("board", "Zündabnehmer (Messboard)"), ("ecu", "rusEFI (Drehzahl vom Steuergerät)"))
COLORS = {"board": "#ef6c00", "ecu": "#1f5fbf"}


class RatioDialog(QtWidgets.QDialog):
    def __init__(self, parent, ctrl, rusefi_page, params: Callable[[], physics.DynoParams],
                 apply: Callable[[float], None], source: str = "board"):
        super().__init__(parent)
        self.setWindowTitle("Übersetzung einmessen")
        self.ctrl = ctrl
        self.rusefi_page = rusefi_page
        self.params = params
        self.apply_ratio = apply
        self.samples = None                   # (Ende, t[], n_rolle[], n_board[], n_ecu[])
        self.result = {}
        self._last_received = -1
        self._ecu_added = False
        self._last = None

        lay = QtWidgets.QVBoxLayout(self)
        g = QtWidgets.QGroupBox("Quelle der Motordrehzahl")
        gl = QtWidgets.QVBoxLayout(g)
        self.radios = {}
        for key, text in SOURCES:
            rb = QtWidgets.QRadioButton(text)
            gl.addWidget(rb)
            self.radios[key] = rb
        self.ecu_hint = QtWidgets.QLabel("")
        self.ecu_hint.setStyleSheet("color: gray;")
        gl.addWidget(self.ecu_hint)
        lay.addWidget(g)

        form = QtWidgets.QFormLayout()
        self.duration = QtWidgets.QDoubleSpinBox()
        self.duration.setRange(1.0, 60.0)
        self.duration.setDecimals(0)
        self.duration.setSuffix(" s")
        self.duration.setValue(5.0)
        form.addRow("Messdauer", self.duration)
        lay.addLayout(form)

        self.table = QtWidgets.QTableWidget(3, 3)
        self.table.setHorizontalHeaderLabels(["n Motor [1/min]", "n Rolle [1/min]", "Übersetzung"])
        self.table.setVerticalHeaderLabels(["Zündabnehmer", "rusEFI", ""])
        self.table.setRowHidden(2, True)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        self.table.setFixedHeight(90)
        lay.addWidget(self.table)
        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        lay.addWidget(self.progress)
        self.plot = pg.PlotWidget(background="w")
        self.plot.setMenuEnabled(False)
        self.plot.hideButtons()
        self.plot.showGrid(x=True, y=True, alpha=0.25)
        self.plot.setLabel("bottom", "Messzeit [s]")
        self.plot.setLabel("left", "Übersetzung nKW/nRolle")
        self.plot.setMouseEnabled(x=False, y=True)
        self.plot.setMinimumHeight(220)
        self.curves = {}
        for key, _text in SOURCES:
            self.curves[key] = self.plot.plot([], [], pen=pg.mkPen(COLORS[key], width=1.2),
                                              symbol="o", symbolSize=4, symbolPen=None,
                                              symbolBrush=COLORS[key], connect="finite")
        self.mean_line = pg.InfiniteLine(angle=0, movable=True, pen=pg.mkPen("#c62828", width=2.5),
                                         hoverPen=pg.mkPen("#ff1744", width=4),
                                         label="{value:.3f}", labelOpts={"position": 0.95, "color": "#c62828",
                                                                         "fill": "#ffffffcc"})
        self.mean_line.setVisible(False)
        self.plot.addItem(self.mean_line, ignoreBounds=True)
        self.mean_line.sigPositionChanged.connect(self._line_moved)
        self.plot.scene().sigMouseClicked.connect(self._plot_clicked)
        lay.addWidget(self.plot, 1)

        vl = QtWidgets.QHBoxLayout()
        vl.addWidget(QtWidgets.QLabel("Übersetzung (rote Linie):"))
        self.value_spin = QtWidgets.QDoubleSpinBox()
        self.value_spin.setRange(0.001, 100.0)
        self.value_spin.setDecimals(3)
        self.value_spin.setSingleStep(0.005)
        self.value_spin.setEnabled(False)
        self.value_spin.valueChanged.connect(self._spin_changed)
        vl.addWidget(self.value_spin)
        self.mean_btn = QtWidgets.QPushButton("Mittelwert")
        self.mean_btn.setToolTip("Linie zurück auf den berechneten (getrimmten) Mittelwert")
        self.mean_btn.setEnabled(False)
        self.mean_btn.clicked.connect(self._reset_line)
        vl.addWidget(self.mean_btn)
        hint = QtWidgets.QLabel("Linie ziehen oder ins Diagramm klicken")
        hint.setStyleSheet("color: gray;")
        vl.addWidget(hint, 1)
        lay.addLayout(vl)

        self.info = QtWidgets.QLabel("Motor auf konstante Drehzahl bringen, dann „Messen“.")
        self.info.setWordWrap(True)
        lay.addWidget(self.info)

        bb = QtWidgets.QHBoxLayout()
        self.measure_btn = QtWidgets.QPushButton("Messen")
        self.apply_btn = QtWidgets.QPushButton("Übernehmen")
        self.apply_btn.setEnabled(False)
        close_btn = QtWidgets.QPushButton("Schließen")
        bb.addWidget(self.measure_btn)
        bb.addStretch(1)
        bb.addWidget(self.apply_btn)
        bb.addWidget(close_btn)
        lay.addLayout(bb)
        self.measure_btn.clicked.connect(self.start)
        self.apply_btn.clicked.connect(self.on_apply)
        close_btn.clicked.connect(self.reject)
        for rb in self.radios.values():
            rb.toggled.connect(lambda on: on and self._source_changed())

        ecu_ok = self._ecu_available()
        self.radios["ecu"].setEnabled(ecu_ok)
        self.ecu_hint.setText("" if ecu_ok else "rusEFI nicht verbunden (Reiter ECU) oder ohne .ini")
        self.radios["ecu" if source == "ecu" and ecu_ok else "board"].setChecked(True)
        self.resize(620, 640)

        if ecu_ok:
            self._add_ecu_rpm()
        link = self.ctrl.link
        if not link.streaming():
            link.start()
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(50)

    # ------------------------------------------------------------------ rusEFI
    def _ecu(self):
        return self.rusefi_page.active_link()

    def _ecu_available(self) -> bool:
        ecu = self._ecu()
        return ecu is not None and ECU_RPM in ecu.ini.channels

    def _add_ecu_rpm(self):
        """Die ECU-Drehzahl wird waehrend des Dialogs mitgelesen, auch wenn sie nicht aufgezeichnet wird."""
        ecu = self._ecu()
        names = ecu.channel_names()
        if ECU_RPM not in names:
            ecu.set_channels(names + [ECU_RPM])
            self._ecu_added = True

    def _ecu_rpm(self) -> Optional[float]:
        ecu = self._ecu()
        return ecu.value(ECU_RPM) if ecu is not None else None

    def done(self, r):
        self.timer.stop()
        ecu = self._ecu()
        if self._ecu_added and ecu is not None:
            ecu.set_channels([n for n in ecu.channel_names() if n != ECU_RPM])
        super().done(r)

    # ------------------------------------------------------------------ Messung
    def source(self) -> str:
        return "ecu" if self.radios["ecu"].isChecked() else "board"

    def start(self):
        self.ctrl.params = self.params()
        self.samples = (time.time() + self.duration.value(), [], [], [], [])
        self._t0 = time.time()
        self.result = {}
        self.mean_line.setVisible(False)
        self.value_spin.setEnabled(False)
        self.mean_btn.setEnabled(False)
        for c in self.curves.values():
            c.setData([], [])
        self.plot.enableAutoRange()
        self.apply_btn.setEnabled(False)
        self.measure_btn.setEnabled(False)
        self.info.setText("Messung läuft – Drehzahl konstant halten …")

    def _check_ecu(self):
        """rusEFI kann sich auch bei offenem Dialog verbinden oder trennen."""
        ok = self._ecu_available()
        if ok != self.radios["ecu"].isEnabled():
            self.radios["ecu"].setEnabled(ok)
            self.ecu_hint.setText("" if ok else "rusEFI nicht verbunden (Reiter ECU) oder ohne .ini")
            if ok:
                self._add_ecu_rpm()
            elif self.radios["ecu"].isChecked():
                self.radios["board"].setChecked(True)

    def _tick(self):
        self._check_ecu()
        link = self.ctrl.link
        if link.received == self._last_received:
            return
        self._last_received = link.received
        lv = self.ctrl.live
        n_ecu = self._ecu_rpm()
        self._set_row(0, lv.n_meas, lv.n_roll)
        self._set_row(1, n_ecu, lv.n_roll)
        if self.samples is None:
            return
        end, ts, nr, nb, ne = self.samples
        ts.append(time.time() - self._t0)
        nr.append(lv.n_roll)
        nb.append(lv.n_meas)
        ne.append(np.nan if n_ecu is None else n_ecu)
        self._draw()
        left = end - time.time()
        self.progress.setValue(int(1000 * (1.0 - max(0.0, left) / self.duration.value())))
        if left <= 0:
            self._finish()

    def _set_row(self, row: int, n_motor, n_roll):
        vals = ["–", "–", "–"]
        if n_motor is not None and np.isfinite(n_motor):
            vals[0] = f"{n_motor:.0f}"
            vals[1] = f"{n_roll:.0f}"
            if n_roll > 1:
                vals[2] = f"{n_motor / n_roll:.3f}"
        if self.result:
            key = "board" if row == 0 else "ecu"
            r = self.result.get(key)
            vals[2] = "–" if r is None else f"{r[0]:.3f}  (± {r[1]:.3f})"
        for c, text in enumerate(vals):
            it = QtWidgets.QTableWidgetItem(text)
            it.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            self.table.setItem(row, c, it)

    def _ratios(self, samples=None):
        """{Quelle: (t, Uebersetzung)} der laufenden oder letzten Messung."""
        _end, ts, nr, nb, ne = samples or self.samples or self._last
        t, nr = np.array(ts), np.array(nr)
        out = {}
        with np.errstate(invalid="ignore", divide="ignore"):
            for key, nm in (("board", np.array(nb)), ("ecu", np.array(ne))):
                r = np.where(nr > 1, nm / nr, np.nan)
                if np.isfinite(r).any():
                    out[key] = (t, r)
        return out

    def _draw(self):
        data = self._ratios()
        for key, c in self.curves.items():
            if key in data:
                c.setData(*data[key])
            else:
                c.setData([], [])

    def _finish(self):
        self._last = self.samples
        _end, _ts, nr, nb, ne = self.samples
        self.samples = None
        nr = np.array(nr)
        for key, nm in (("board", np.array(nb)), ("ecu", np.array(ne))):
            ok = np.isfinite(nm) & (nr > 1)
            r = physics.trimmed_ratio(nm[ok], nr[ok])
            if r is not None:
                spread = float(np.std(nm[ok] / nr[ok]))
                self.result[key] = (r, spread)
        self.measure_btn.setEnabled(True)
        self.progress.setValue(1000)
        self._draw()
        self._reset_line()

    def _show_result(self):
        if not self.result:
            return
        r = self.result.get(self.source())
        self.apply_btn.setEnabled(r is not None)
        lines = []
        if r is None:
            lines.append("Für die gewählte Quelle zu wenige Daten (laufen Motor und Rolle?).")
        else:
            v = self.value()
            lines.append(f"Mittelwert ({dict(SOURCES)[self.source()]}): {r[0]:.3f}"
                         + (f"  →  gewählt: <b>{v:.3f}</b>" if v and abs(v - r[0]) >= 0.0005 else
                            f"  →  <b>{r[0]:.3f}</b>"))
            if r[1] > 0.02 * r[0]:
                lines.append("Streuung groß – Drehzahl war nicht konstant oder das Signal ist unsauber.")
        b, e = self.result.get("board"), self.result.get("ecu")
        if b and e and abs(b[0] / e[0] - 1.0) > 0.03:
            lines.append(f"Zündabnehmer und rusEFI weichen um {100 * (b[0] / e[0] - 1):+.0f} % ab "
                         f"(Faktor {b[0] / e[0]:.2f}) – Impulse je Umdrehung prüfen.")
        self.info.setText("<br>".join(lines))

    # ------------------------------------------------------------------ Linie
    def _source_changed(self):
        if self.result:
            self._reset_line()

    def _reset_line(self):
        r = self.result.get(self.source())
        on = r is not None
        self.mean_line.setVisible(on)
        self.value_spin.setEnabled(on)
        self.mean_btn.setEnabled(on)
        if on:
            self.mean_line.setPen(pg.mkPen("#c62828", width=2.5))
            self._set_value(r[0])
            # Achse auf die Werte der gewaehlten Quelle (Ausreisser anderer Quellen nicht mitzoomen)
            _t, y = self._ratios(self._last)[self.source()]
            y = y[np.isfinite(y)]
            lo, hi = float(np.min(y)), float(np.max(y))
            pad = max((hi - lo) * 0.08, 0.02 * r[0])
            self.plot.setYRange(lo - pad, hi + pad, padding=0)
        self._show_result()

    def _set_value(self, v: float):
        self._updating = True
        self.mean_line.setValue(v)
        self.value_spin.setValue(v)
        self._updating = False

    def _line_moved(self):
        if getattr(self, "_updating", False):
            return
        self._updating = True
        self.value_spin.setValue(round(self.mean_line.value(), 3))
        self._updating = False
        self._show_result()

    def _spin_changed(self, v):
        if getattr(self, "_updating", False):
            return
        self._updating = True
        self.mean_line.setValue(v)
        self._updating = False
        self._show_result()

    def _plot_clicked(self, ev):
        if not self.mean_line.isVisible() or ev.button() != QtCore.Qt.LeftButton:
            return
        vb = self.plot.getPlotItem().vb
        if not vb.sceneBoundingRect().contains(ev.scenePos()):
            return
        self._set_value(round(vb.mapSceneToView(ev.scenePos()).y(), 3))
        self._show_result()

    def value(self) -> Optional[float]:
        return round(self.value_spin.value(), 3) if self.value_spin.isEnabled() else None

    def on_apply(self):
        v = self.value()
        if v:
            self.apply_ratio(v)
            self.info.setText(self.info.text() + "<br>✓ übernommen")
