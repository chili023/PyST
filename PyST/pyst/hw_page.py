"""
Reiter „Zange · Netz · Steckdosen“ (PyST-Basis, Funk-Zange, Zange per USB, WLAN-Steckdosen).

- Zange: Akku, Signal, Sync, Funken, Blitz φ; Koppeln, gekoppelte Geräte, Markieren, Aufnahme, Identifizieren,
  Ausschalten, Einstellungen (#ZANGE SET/GET). Zange per USB: Warnhinweis (galvanische Verbindung),
  Log auslesen -> Dauermessung / .pyst.
- Netzwerk: WLAN der Basis (Access Point / im Werkstattnetz / aus), PC per WLAN mit der Basis verbinden.
- Steckdosen: über die Basis oder direkt vom PC (Shelly Gen1/Gen2+, Tasmota, ESPHome), Ein/Aus, Leistung,
  Totmann-Zeit, Automatik (beim Laufstart ein, nach Laufende + Nachlaufzeit aus).
Netzwerk- und Funkzugriffe laufen im Hintergrund, damit die Oberfläche nicht hängt.
"""
import os
import queue
import threading
import time
from typing import Callable, Dict, List, Optional

from PySide6 import QtCore, QtGui, QtWidgets

from . import storage
from .basis import (MAX_PLUGS, PLUG_TYPE_TEXT, PLUG_TYPES, ZANGE_KEYS, ZANGE_USB_WARNUNG, NetState, ZangeStatus,
                    main_zange, net_commands)
from .plugs import NO_FALLBACK, PlugConfig, PlugManager, start_demo_plugs

RED_BANNER = "background: #c62828; color: white; font-weight: 700; padding: 6px; border-radius: 4px;"


class _Bg:
    """Ein Hintergrund-Thread für Befehle an die Basis; Ergebnisse holt poll() im GUI-Takt ab."""

    def __init__(self):
        self.jobs: "queue.Queue" = queue.Queue()
        self.done: "queue.Queue" = queue.Queue()
        self.pending = set()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while True:
            key, fn, cb = self.jobs.get()
            try:
                res, err = fn(), None
            except Exception as exc:
                res, err = None, exc
            self.done.put((key, cb, res, err))

    def submit(self, key: str, fn: Callable, cb: Callable):
        if key in self.pending:
            return
        self.pending.add(key)
        self.jobs.put((key, fn, cb))

    def poll(self):
        while True:
            try:
                key, cb, res, err = self.done.get_nowait()
            except queue.Empty:
                return
            self.pending.discard(key)
            cb(res, err)


class ZangeSettingsDialog(QtWidgets.QDialog):
    """Einstellungen der Zange über die Basis (#ZANGE GET/SET)."""

    def __init__(self, link, parent=None):
        super().__init__(parent)
        self.link = link
        self.setWindowTitle("Zange – Einstellungen")
        self.resize(640, 460)
        lay = QtWidgets.QVBoxLayout(self)
        lay.addWidget(QtWidgets.QLabel("Werte werden per Funk an die Zange übertragen (Antwort nach Quittung)."))
        self.table = QtWidgets.QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Einstellung", "Wert", "Bedeutung"])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.Stretch)
        lay.addWidget(self.table, 1)
        self.msg = QtWidgets.QLabel("")
        self.msg.setWordWrap(True)
        lay.addWidget(self.msg)
        bb = QtWidgets.QHBoxLayout()
        self.b_reload = QtWidgets.QPushButton("Neu lesen")
        self.b_apply = QtWidgets.QPushButton("Übernehmen")
        close = QtWidgets.QPushButton("Schließen")
        bb.addWidget(self.b_reload)
        bb.addWidget(self.b_apply)
        bb.addStretch(1)
        bb.addWidget(close)
        lay.addLayout(bb)
        self.b_reload.clicked.connect(self.reload)
        self.b_apply.clicked.connect(self.apply)
        close.clicked.connect(self.accept)
        self.values: Dict[str, str] = {}
        self.reload()

    def reload(self):
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            items = self.link.zange_get_all()
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        self.values = dict(items)
        self.table.setRowCount(len(ZANGE_KEYS))
        for r, (k, (help_, _d)) in enumerate(ZANGE_KEYS.items()):
            a = QtWidgets.QTableWidgetItem(k)
            a.setFlags(a.flags() & ~QtCore.Qt.ItemIsEditable)
            c = QtWidgets.QTableWidgetItem(help_)
            c.setFlags(c.flags() & ~QtCore.Qt.ItemIsEditable)
            self.table.setItem(r, 0, a)
            self.table.setItem(r, 1, QtWidgets.QTableWidgetItem(self.values.get(k, "")))
            self.table.setItem(r, 2, c)
        self.table.resizeColumnToContents(0)
        self.msg.setText(f"{len(items)} von {len(ZANGE_KEYS)} Einstellungen gelesen." if items
                         else "Keine Antwort der Zange (nicht gekoppelt oder außer Reichweite?).")

    def apply(self) -> bool:
        errors, n = [], 0
        for r in range(self.table.rowCount()):
            k = self.table.item(r, 0).text()
            v = self.table.item(r, 1).text().strip()
            if v and v != self.values.get(k):
                lines = self.link.zange_set(k, v)
                if self.link.ok(lines):
                    self.values[k] = v
                    n += 1
                else:
                    errors.append(f"{k}: {lines[-1] if lines else 'keine Antwort'}")
        self.msg.setText(f"{n} Einstellung(en) übertragen." + ("\nFehler: " + "; ".join(errors) if errors else ""))
        return not errors


class PlugDialog(QtWidgets.QDialog):
    """Steckdose anlegen/bearbeiten."""

    def __init__(self, cfg: PlugConfig, direct: bool, parent=None, found: Optional[List[dict]] = None):
        super().__init__(parent)
        self.setWindowTitle(f"Steckdose {cfg.nr}")
        form = QtWidgets.QFormLayout(self)
        self.nr = QtWidgets.QSpinBox()
        self.nr.setRange(1, MAX_PLUGS)
        self.nr.setValue(cfg.nr)
        self.name = QtWidgets.QLineEdit(cfg.name)
        self.name.setPlaceholderText("z.B. Gebläse, Absaugung, Heizlüfter")
        self.typ = QtWidgets.QComboBox()
        for t in PLUG_TYPES:
            self.typ.addItem(PLUG_TYPE_TEXT[t], t)
        self.typ.setCurrentIndex(max(0, PLUG_TYPES.index(cfg.typ) if cfg.typ in PLUG_TYPES else 0))
        self.host = QtWidgets.QComboBox()
        self.host.setEditable(True)
        for f in found or []:
            self.host.addItem(f"{f.get('host')}", f)
        self.host.setEditText(cfg.host)
        self.host.currentIndexChanged.connect(self._found_chosen)
        self.channel = QtWidgets.QSpinBox()
        self.channel.setRange(0, 3)
        self.channel.setValue(cfg.channel)
        self.esp_id = QtWidgets.QLineEdit(cfg.esphome_id)
        self.power_id = QtWidgets.QLineEdit(cfg.power_id)
        self.fail = QtWidgets.QComboBox()
        self.fail.addItems(["fällt aus (z.B. Heizlüfter)", "bleibt an (z.B. Absaugung, Lüfter)"])
        self.fail.setCurrentIndex(1 if cfg.fail_on else 0)
        self.totmann = QtWidgets.QSpinBox()
        self.totmann.setRange(5, 3600)
        self.totmann.setSuffix(" s")
        self.totmann.setValue(int(cfg.totmann_s))
        self.auto_lauf = QtWidgets.QCheckBox("beim Laufstart ein")
        self.auto_lauf.setChecked(cfg.auto_lauf)
        self.auto_dauer = QtWidgets.QCheckBox("während der Dauermessung ein")
        self.auto_dauer.setChecked(cfg.auto_dauer)
        self.nachlauf = QtWidgets.QDoubleSpinBox()
        self.nachlauf.setRange(0, 3600)
        self.nachlauf.setSuffix(" s")
        self.nachlauf.setValue(cfg.nachlauf_s)
        form.addRow("Nummer", self.nr)
        form.addRow("Name", self.name)
        form.addRow("Typ", self.typ)
        form.addRow("IP / Name", self.host)
        if direct:
            form.addRow("Relais-Kanal", self.channel)
            form.addRow("ESPHome Switch-ID", self.esp_id)
            form.addRow("ESPHome Leistungs-Sensor", self.power_id)
        form.addRow("Bei Ausfall von PyST/Basis", self.fail)
        form.addRow("Totmann-Zeit", self.totmann)
        form.addRow("Automatik", self.auto_lauf)
        form.addRow("", self.auto_dauer)
        form.addRow("Nachlaufzeit", self.nachlauf)
        note = QtWidgets.QLabel("Komfortfunktion, keine Sicherheitsfunktion – kein Not-Aus über WLAN.\n"
                                "„fällt aus“: Einschalten immer mit Rückfallzeit (Totmann), wird laufend erneuert.\n"
                                "ESPHome kennt keine Rückfallzeit – dort nur zyklisch erneuert.")
        note.setStyleSheet("color: gray;")
        form.addRow(note)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

    def _found_chosen(self, i: int):
        f = self.host.itemData(i)
        if isinstance(f, dict):
            if f.get("typ") in PLUG_TYPES:
                self.typ.setCurrentIndex(PLUG_TYPES.index(f["typ"]))
            if f.get("name") and not self.name.text():
                self.name.setText(str(f["name"]))

    def config(self) -> PlugConfig:
        return PlugConfig(self.nr.value(), self.typ.currentData(), self.host.currentText().strip(),
                          self.name.text().strip(), self.channel.value(), self.esp_id.text().strip() or "relay",
                          self.power_id.text().strip(), self.fail.currentIndex() == 1, self.totmann.value(),
                          self.auto_lauf.isChecked(), self.auto_dauer.isChecked(), self.nachlauf.value())


class HardwarePage(QtWidgets.QScrollArea):
    zange_text = QtCore.Signal(str)          # Kurztext für die Statusleiste
    log_saved = QtCore.Signal(str)           # ausgelesenes Zangen-Log (Ordner) -> Datenbank
    connect_port = QtCore.Signal(str)        # „Per WLAN verbinden“ -> Hauptfenster

    def __init__(self, settings: dict, save_settings: Callable[[], None], get_link: Callable[[], object],
                 get_params: Callable, runs_dir: Callable[[], str]):
        super().__init__()
        self.settings = settings
        self.save_settings = save_settings
        self.get_link = get_link
        self.get_params = get_params
        self.runs_dir = runs_dir
        self.link = None
        self.bg = _Bg()
        self.zst: Optional[ZangeStatus] = None
        self.demo = []                         # simulierte Steckdosen (Demo)
        self._last_zange = 0.0
        self._last_plugs = 0.0
        self._last_net = 0.0
        self.setWidgetResizable(True)
        inner = QtWidgets.QWidget()
        self.setWidget(inner)
        root = QtWidgets.QVBoxLayout(inner)
        self.banner = QtWidgets.QLabel("ACHTUNG: " + ZANGE_USB_WARNUNG.replace("\n", " "))
        self.banner.setStyleSheet(RED_BANNER)
        self.banner.setWordWrap(True)
        self.banner.hide()
        root.addWidget(self.banner)
        top = QtWidgets.QHBoxLayout()
        top.addWidget(self._build_zange(), 3)
        top.addWidget(self._build_net(), 2)
        root.addLayout(top)
        root.addWidget(self._build_plugs(), 1)
        mode = settings.get("steckdosen_weg", "direkt")
        key = "steckdosen" if mode == "direkt" else "steckdosen_basis"
        self.plugs = PlugManager([PlugConfig.from_dict(d) for d in settings.get(key, [])], mode=mode,
                                 get_link=self.get_link)
        self.plug_mode.setCurrentIndex(0 if mode == "basis" else 1)
        self.plug_mode.currentIndexChanged.connect(self._plug_mode_changed)
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(250)
        self._link_changed()

    # ------------------------------------------------------------------ Aufbau
    def _build_zange(self):
        g = QtWidgets.QGroupBox("Zange")
        lay = QtWidgets.QVBoxLayout(g)
        grid = QtWidgets.QGridLayout()
        self.z_vals: Dict[str, QtWidgets.QLabel] = {}
        for i, (k, label) in enumerate((("verb", "Verbindung"), ("akku", "Akku"), ("rssi", "Signal"),
                                        ("sync", "Sync"), ("funken", "Funken"), ("blitz", "Blitz φ"),
                                        ("log", "Log"), ("fw", "Firmware"))):
            t = QtWidgets.QLabel(label)
            t.setStyleSheet("color: gray;")
            v = QtWidgets.QLabel("–")
            v.setStyleSheet("font-size: 15px; font-weight: 600;")
            grid.addWidget(t, (i // 4) * 2, i % 4)
            grid.addWidget(v, (i // 4) * 2 + 1, i % 4)
            self.z_vals[k] = v
        lay.addLayout(grid)
        self.z_weitere = QtWidgets.QLabel("")
        self.z_weitere.setStyleSheet("color: #555;")
        self.z_weitere.setToolTip("Weitere gekoppelte Zangen; den Zündkanal liefert die Hauptzange (Basis: zange_id).")
        self.z_weitere.setVisible(False)
        lay.addWidget(self.z_weitere)
        row = QtWidgets.QHBoxLayout()
        self.b_pair = QtWidgets.QPushButton("Koppeln …")
        self.b_pair.setToolTip("Kopplungsfenster der Basis öffnen (30 s). An der Zange T1 + T2 gleichzeitig 3 s halten.")
        self.b_mark = QtWidgets.QPushButton("Markieren (F3)")
        self.b_rec = QtWidgets.QPushButton("Aufnahme Zange")
        self.b_rec.setCheckable(True)
        self.b_ident = QtWidgets.QPushButton("Identifizieren")
        self.b_set = QtWidgets.QPushButton("Einstellungen …")
        self.b_more = QtWidgets.QToolButton()
        self.b_more.setText("Weiteres ▾")
        self.b_more.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        m = QtWidgets.QMenu(self.b_more)
        m.addAction("Gekoppelte Geräte …", self.show_pairs)
        m.addAction("Log nachladen (Lücken)", lambda: self._zcmd("LOG"))
        m.addAction("Zange ausschalten", lambda: self._zcmd("OFF"))
        self.b_more.setMenu(m)
        self.b_log = QtWidgets.QPushButton("Log auslesen …")
        self.b_log.setToolTip("Zange per USB: Log auslesen und als Dauermessung (und .pyst) speichern")
        for b in (self.b_pair, self.b_mark, self.b_rec, self.b_ident, self.b_set, self.b_log):
            row.addWidget(b)
        row.addWidget(self.b_more)
        row.addStretch(1)
        lay.addLayout(row)
        self.z_msg = QtWidgets.QLabel("")
        self.z_msg.setWordWrap(True)
        self.z_msg.setStyleSheet("color: #555;")
        lay.addWidget(self.z_msg)
        self.b_pair.clicked.connect(self.pair)
        self.b_mark.clicked.connect(self.mark)
        self.b_rec.toggled.connect(lambda on: self._zcmd("REC 1" if on else "REC 0"))
        self.b_ident.clicked.connect(lambda: self._zcmd("IDENT"))
        self.b_set.clicked.connect(self.zange_settings)
        self.b_log.clicked.connect(lambda: self.read_log())
        QtGui.QShortcut(QtGui.QKeySequence("F3"), self, activated=self.mark, context=QtCore.Qt.ApplicationShortcut)
        return g

    def _build_net(self):
        g = QtWidgets.QGroupBox("Netzwerk (WLAN der Basis)")
        form = QtWidgets.QFormLayout(g)
        self.net_state = QtWidgets.QLabel("–")
        self.net_state.setWordWrap(True)
        form.addRow("Zustand", self.net_state)
        self.net_mode = QtWidgets.QComboBox()
        self.net_mode.addItem("Access Point (Basis macht eigenes WLAN)", "AP")
        self.net_mode.addItem("Im Werkstatt-WLAN anmelden", "STA")
        self.net_mode.addItem("WLAN aus", "OFF")
        self.net_ch = QtWidgets.QSpinBox()
        self.net_ch.setRange(1, 13)
        self.net_ch.setValue(6)
        self.net_ssid = QtWidgets.QLineEdit("PyST-Basis")
        self.net_pw = QtWidgets.QLineEdit()
        self.net_pw.setEchoMode(QtWidgets.QLineEdit.PasswordEchoOnEdit)
        self.net_pw.setPlaceholderText("mind. 8 Zeichen, keine Leerzeichen")
        form.addRow("Betrieb", self.net_mode)
        form.addRow("Kanal (AP)", self.net_ch)
        form.addRow("SSID", self.net_ssid)
        form.addRow("Passwort", self.net_pw)
        row = QtWidgets.QHBoxLayout()
        self.b_net_apply = QtWidgets.QPushButton("Übernehmen")
        self.b_net_read = QtWidgets.QPushButton("Neu lesen")
        self.b_tcp = QtWidgets.QPushButton("PC per WLAN verbinden")
        self.b_tcp.setToolTip("Statt USB: PyST verbindet sich über TCP (Port 2323) mit der Basis")
        for b in (self.b_net_apply, self.b_net_read, self.b_tcp):
            row.addWidget(b)
        form.addRow(row)
        self.net_msg = QtWidgets.QLabel("Der Kanal gilt auch für den Funk zur Zange (ESP-NOW).")
        self.net_msg.setWordWrap(True)
        self.net_msg.setStyleSheet("color: gray;")
        form.addRow(self.net_msg)
        self.net_mode.currentIndexChanged.connect(self._net_mode_ui)
        self.b_net_apply.clicked.connect(self.net_apply)
        self.b_net_read.clicked.connect(lambda: self._refresh_net(force=True))
        self.b_tcp.clicked.connect(self._connect_tcp)
        self._net_mode_ui()
        return g

    def _build_plugs(self):
        g = QtWidgets.QGroupBox("Steckdosen (Gebläse, Absaugung, Heizlüfter …)")
        lay = QtWidgets.QVBoxLayout(g)
        bar = QtWidgets.QHBoxLayout()
        bar.addWidget(QtWidgets.QLabel("Schalten über"))
        self.plug_mode = QtWidgets.QComboBox()
        self.plug_mode.addItem("die Basis", "basis")
        self.plug_mode.addItem("direkt vom PC (gleiches Netz, ohne Basis)", "direkt")
        bar.addWidget(self.plug_mode)
        bar.addStretch(1)
        self.b_demo = QtWidgets.QPushButton("Demo-Steckdosen (Simulator)")
        self.b_demo.setToolTip("Vier simulierte Steckdosen (Shelly Gen2, Shelly Gen1, Tasmota, ESPHome) auf diesem PC")
        bar.addWidget(self.b_demo)
        lay.addLayout(bar)
        self.ptable = QtWidgets.QTableWidget(0, 9)
        self.ptable.setHorizontalHeaderLabels(["Nr", "Name", "Typ", "IP / Name", "Zustand", "Leistung [W]",
                                               "Totmann", "Bei Ausfall", "Automatik"])
        self.ptable.verticalHeader().setVisible(False)
        self.ptable.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.ptable.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        hh = self.ptable.horizontalHeader()
        for col in range(8):
            hh.setSectionResizeMode(col, QtWidgets.QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(8, QtWidgets.QHeaderView.Stretch)
        self.ptable.cellDoubleClicked.connect(lambda r, _c: self.edit_plug())
        lay.addWidget(self.ptable, 1)
        row = QtWidgets.QHBoxLayout()
        self.b_on = QtWidgets.QPushButton("Ein")
        self.b_off = QtWidgets.QPushButton("Aus")
        self.b_add = QtWidgets.QPushButton("Hinzufügen …")
        self.b_scan = QtWidgets.QPushButton("Suchen")
        self.b_edit = QtWidgets.QPushButton("Bearbeiten …")
        self.b_del = QtWidgets.QPushButton("Entfernen")
        self.b_prefresh = QtWidgets.QPushButton("Aktualisieren")
        for b in (self.b_on, self.b_off, self.b_add, self.b_scan, self.b_edit, self.b_del, self.b_prefresh):
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        self.p_msg = QtWidgets.QLabel("Komfortfunktion, keine Sicherheitsfunktion – kein Not-Aus über WLAN.")
        self.p_msg.setWordWrap(True)
        self.p_msg.setStyleSheet("color: gray;")
        lay.addWidget(self.p_msg)
        self.b_on.clicked.connect(lambda: self._switch_selected(True))
        self.b_off.clicked.connect(lambda: self._switch_selected(False))
        self.b_add.clicked.connect(lambda: self.add_plug())
        self.b_scan.clicked.connect(self.scan_plugs)
        self.b_edit.clicked.connect(self.edit_plug)
        self.b_del.clicked.connect(self.delete_plug)
        self.b_prefresh.clicked.connect(lambda: self.plugs.refresh())
        self.b_demo.clicked.connect(self.start_demo)
        return g

    # ------------------------------------------------------------------ Gerät
    def _link_changed(self):
        link = self.link
        basis = bool(link and link.is_basis)
        usb = bool(link and link.is_zange_usb)
        self.banner.setVisible(usb)
        for b in (self.b_pair, self.b_more):
            b.setEnabled(basis)
        zange_ok = basis and link.ign_source == "zange"
        for b in (self.b_mark, self.b_rec, self.b_ident, self.b_set):
            b.setEnabled(zange_ok)
        self.b_log.setVisible(usb)
        self.b_log.setEnabled(usb)
        for w in (self.net_mode, self.net_ch, self.net_ssid, self.net_pw, self.b_net_apply, self.b_net_read):
            w.setEnabled(basis)
        if not basis:
            self.net_state.setText("–")
        if usb:
            self._show_zange(None)
            self.z_vals["verb"].setText("per USB")
            self.z_vals["fw"].setText(link.caps.get("fw", ""))
            self.z_msg.setText("Zange direkt per USB: Zündungs-Oszi im Reiter „Oszi / Dauermessung“ "
                               "(nur Kanal Zündung), Log auslesen hier.")
        elif link is None:
            self._show_zange(None)
            self.z_msg.setText("Keine Basis verbunden (USB oder WLAN, oder Simulator „SIM:basis“).")
        elif not basis:
            self._show_zange(None)
            self.z_msg.setText("Das verbundene Gerät ist keine PyST-Basis – Zange und Netzwerk nicht verfügbar.")
        elif not zange_ok:
            self._show_zange(None)
            self.z_msg.setText("Keine Zange gekoppelt – „Koppeln …“ drücken.")
        else:
            self.z_msg.setText("")
        self._last_zange = self._last_net = 0.0

    def _check_link(self):
        link = self.get_link()
        if link is not self.link:
            self.link = link
            self.zst = None
            self._link_changed()

    def _show_zange(self, st: Optional[ZangeStatus]):
        v = self.z_vals
        if st is None:
            for k in v:
                v[k].setText("–")
            self.zange_text.emit("")
            return
        nan = lambda x: x != x
        v["verb"].setText(("verbunden" if st.connected else "nicht erreichbar") + (f" (ID {st.id})" if st.id else ""))
        v["verb"].setStyleSheet("font-size: 15px; font-weight: 600; color: %s;" % ("#2e7d32" if st.connected
                                                                                   else "#c62828"))
        v["akku"].setText("–" if nan(st.akku_pct) else f"{st.akku_pct:.0f} %" +
                          ("" if nan(st.akku_mv) else f" ({st.akku_mv / 1000:.2f} V)") + (" lädt" if st.vbus else ""))
        v["rssi"].setText("–" if nan(st.rssi) else f"{st.rssi:.0f} dBm")
        v["sync"].setText(f"ok (±{st.sync_us:.0f} µs)" if st.sync_ok else "kein Sync")
        v["funken"].setText(f"{st.funken:,}".replace(",", "."))
        v["blitz"].setText(("aus" if not st.blitz else ("Trigger" if st.blitz == 1 else "Funken"))
                           + ("" if nan(st.phi) or not st.blitz else f" · φ {st.phi:.1f}°"))
        v["log"].setText(st.log or "–")
        v["fw"].setText(st.fw or "–")
        self.zange_text.emit(st.summary())

    # ------------------------------------------------------------------ Zange
    def _zcmd(self, sub: str):
        link = self.link
        if link is None:
            return
        self.bg.submit("z" + sub, lambda: link.zange_cmd(sub),
                       lambda res, err: self.z_msg.setText(
                           f"{sub}: " + (str(err) if err else (res[-1] if res else "keine Antwort"))))

    def mark(self):
        if self.link is not None and self.link.ign_source == "zange":
            self._zcmd("MARK")

    def pair(self):
        link = self.link
        if link is None or not link.is_basis:
            return
        if not link.pair(True):
            self.z_msg.setText("Basis lehnt das Koppeln ab.")
            return
        self.z_msg.setText("Kopplungsfenster 30 s offen – an der Zange T1 + T2 gleichzeitig 3 s halten …")

    def show_pairs(self):
        link = self.link
        if link is None:
            return
        entries = link.pair_list()
        if not entries:
            QtWidgets.QMessageBox.information(self, "Gekoppelte Geräte", "Keine Geräte gekoppelt.")
            return
        items = [f"ID {e.id}: {e.typ} {e.sn} · {e.mac} · FW {e.fw}" + ("" if e.connected is None else
                                                                       (" · verbunden" if e.connected else " · weg"))
                 for e in entries]
        item, ok = QtWidgets.QInputDialog.getItem(self, "Gekoppelte Geräte", "Gerät (OK = entkoppeln):", items, 0,
                                                  False)
        if ok and QtWidgets.QMessageBox.question(self, "Entkoppeln", f"{item}\nwirklich entkoppeln?") == \
                QtWidgets.QMessageBox.Yes:
            e = entries[items.index(item)]
            self.z_msg.setText("Entkoppelt." if link.pair_delete(e.id) else "Basis lehnt ab.")
            link.probe_caps()
            self._link_changed()

    def zange_settings(self):
        if self.link is not None and self.link.ign_source == "zange":
            ZangeSettingsDialog(self.link, self).exec()

    def on_event(self, ev):
        """Ereignis der Basis (aus dem Hauptfenster)."""
        if ev.kind in ("ZANGE", "PAIR") and self.link is not None:
            self.link.probe_caps()                         # ign=zange|none kann sich geändert haben
            self._link_changed()
            self.z_msg.setText(ev.text)
        elif ev.kind == "DOSE":
            self.plugs.refresh()

    def read_log(self, save_pyst: Optional[str] = None, ask: bool = True) -> Optional[str]:
        """Zange per USB: Log auslesen -> Dauermessung (Ordner) und optional .pyst. Liefert den Ordner."""
        from . import zange
        link = self.link
        if link is None or not link.is_zange_usb:
            return None
        was = link.streaming()
        if was:
            link.ensure_stopped()
        dlg = QtWidgets.QProgressDialog("Log der Zange wird gelesen …", "", 0, 100, self)
        dlg.setCancelButton(None)
        dlg.setMinimumDuration(0)
        dlg.setWindowModality(QtCore.Qt.WindowModal)

        def progress(done, total):
            dlg.setValue(int(100 * done / max(1, total)))
            QtWidgets.QApplication.processEvents()
        try:
            entries = link.log_read_all(progress=progress)
            settings = dict(link.get_settings())
        except Exception as exc:
            dlg.close()
            self.z_msg.setText(f"Log lesen fehlgeschlagen: {exc}")
            return None
        finally:
            if was:
                link.start()
        dlg.close()
        if not entries:
            self.z_msg.setText("Log ist leer.")
            return None
        folder = zange.write_log_folder(entries, zange.log_folder_name(self.runs_dir()), self.get_params(),
                                        firmware=link.caps.get("fw", ""), port=link.port, settings=settings)
        self.log_saved.emit(folder)
        msg = f"{len(entries)} Log-Einträge gelesen → Dauermessung „{os.path.basename(folder)}“ (Laufliste)."
        if save_pyst is None and ask:
            save_pyst, _ = QtWidgets.QFileDialog.getSaveFileName(
                self, "Zangen-Log weitergeben (.pyst)", os.path.join(os.path.expanduser("~"),
                                                                     os.path.basename(folder) + ".pyst"),
                "PyST-Dateien (*.pyst)")
        if save_pyst:
            path = storage.export_pyst(save_pyst, [{"ordner": folder, "name": os.path.basename(folder),
                                                    "datum": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                                    "quelle": "Dauermessung", "art": "dauermessung",
                                                    "notiz": "Zange-Log"}])
            msg += f" Gespeichert: {path}"
        self.z_msg.setText(msg)
        return folder

    # ------------------------------------------------------------------ Netzwerk
    def _net_mode_ui(self, *_):
        m = self.net_mode.currentData()
        self.net_ch.setEnabled(m == "AP" and self.net_mode.isEnabled())
        self.net_ssid.setEnabled(m != "OFF" and self.net_mode.isEnabled())
        self.net_pw.setEnabled(m != "OFF" and self.net_mode.isEnabled())

    def _show_net(self, st: Optional[NetState], err=None):
        if err is not None or st is None:
            self.net_state.setText("keine Antwort" if err is None else str(err))
            return
        self.net_state.setText(st.summary())

    def _refresh_net(self, force: bool = False):
        link = self.link
        if link is None or not link.is_basis:
            return
        self.bg.submit("net", link.net_state, lambda res, err: self._show_net(res, err))

    def net_apply(self) -> bool:
        link = self.link
        if link is None:
            return False
        try:
            cmd = net_commands(self.net_mode.currentData(), self.net_ssid.text().strip(), self.net_pw.text(),
                               self.net_ch.value())
        except ValueError as exc:
            self.net_msg.setText(str(exc))
            return False
        if link.is_tcp and QtWidgets.QMessageBox.question(
                self, "Netzwerk", "PyST ist per WLAN mit der Basis verbunden – die Verbindung reißt beim Umstellen "
                                  "ab. Trotzdem übernehmen?") != QtWidgets.QMessageBox.Yes:
            return False
        lines = link.command(cmd, timeout=3.0)
        ok = link.ok(lines)
        self.net_msg.setText("Übernommen." if ok else "Abgelehnt: " + (lines[-1] if lines else "keine Antwort"))
        self._refresh_net(force=True)
        return ok

    def _connect_tcp(self):
        from .link import TCP_DEFAULT
        host = TCP_DEFAULT
        st = self.net_state.text()
        if "IP " in st:
            ip = st.split("IP ", 1)[1].split()[0]
            host = f"tcp://{ip}:2323"
        self.connect_port.emit(host)

    # ------------------------------------------------------------------ Steckdosen
    def _plug_mode_changed(self, i: int):
        mode = self.plug_mode.itemData(i)
        self.plugs.mode = mode
        self.plugs.states.clear()
        if mode == "direkt":
            self.plugs.configs = {c["nr"]: PlugConfig.from_dict(c) for c in self.settings.get("steckdosen", [])}
        else:
            self.plugs.configs = {c["nr"]: PlugConfig.from_dict(c) for c in self.settings.get("steckdosen_basis", [])}
        self.settings["steckdosen_weg"] = mode
        self.save_settings()
        self.plugs.refresh()
        self._fill_plugs()

    def _persist_plugs(self):
        key = "steckdosen" if self.plugs.mode == "direkt" else "steckdosen_basis"
        self.settings[key] = self.plugs.to_settings()
        self.save_settings()

    def _selected_nr(self) -> Optional[int]:
        r = self.ptable.currentRow()
        if r < 0 or self.ptable.item(r, 0) is None:
            return None
        return int(self.ptable.item(r, 0).text())

    def _switch_selected(self, on: bool):
        nr = self._selected_nr()
        if nr is not None:
            self.plugs.switch(nr, on)

    def add_plug(self, cfg: Optional[PlugConfig] = None, ask: bool = True):
        nr = self.plugs.free_nr()
        if not nr:
            self.p_msg.setText(f"Höchstens {MAX_PLUGS} Steckdosen.")
            return
        cfg = cfg or PlugConfig(nr, "SHELLY2", "", totmann_s=60)
        if ask:
            dlg = PlugDialog(cfg, self.plugs.mode == "direkt", self, self.plugs.found)
            if dlg.exec() != QtWidgets.QDialog.Accepted:
                return
            cfg = dlg.config()
        if not cfg.host:
            self.p_msg.setText("IP-Adresse oder Name fehlt.")
            return
        try:
            self.plugs.add(cfg)
        except ValueError as exc:
            self.p_msg.setText(str(exc))
            return
        self._persist_plugs()
        self._fill_plugs()

    def edit_plug(self):
        nr = self._selected_nr()
        if nr is None or nr not in self.plugs.configs:
            return
        dlg = PlugDialog(self.plugs.configs[nr], self.plugs.mode == "direkt", self, self.plugs.found)
        dlg.nr.setEnabled(False)
        if dlg.exec() == QtWidgets.QDialog.Accepted:
            c = dlg.config()
            old = self.plugs.configs[nr]
            self.plugs.configs[nr] = c
            if (c.typ, c.host) != (old.typ, old.host):
                self.plugs.add(c)
            self._persist_plugs()
            self._fill_plugs()

    def delete_plug(self):
        nr = self._selected_nr()
        if nr is None:
            return
        self.plugs.delete(nr)
        self._persist_plugs()
        self._fill_plugs()

    def scan_plugs(self):
        self.p_msg.setText("Suche Steckdosen im Netz (mDNS) …")
        self.plugs.scan()

    def start_demo(self):
        """Simulierte Steckdosen auf diesem PC anlegen (direkt vom PC)."""
        if self.plugs.mode != "direkt":
            self.plug_mode.setCurrentIndex(1)
        for dev, cfg in start_demo_plugs():
            self.demo.append(dev)
            cfg.nr = self.plugs.free_nr()
            if not cfg.nr:
                break
            self.plugs.add(cfg)
        self._persist_plugs()
        self.p_msg.setText("Demo-Steckdosen laufen auf 127.0.0.1 (Shelly Gen2, Shelly Gen1, Tasmota, ESPHome). "
                           "Automatik: Gebläse und Absaugung beim Laufstart ein.")
        self._fill_plugs()

    def _fill_plugs(self):
        t = self.ptable
        cfgs = sorted(self.plugs.configs.values(), key=lambda c: c.nr)
        cur = self._selected_nr()
        t.setRowCount(len(cfgs))
        now = time.time()
        for r, c in enumerate(cfgs):
            st = self.plugs.states.get(c.nr)
            state = st.state_text() if st else "?"
            if st and st.on and st.rest_s == st.rest_s and st.rest_s > 0:
                state += f" (Rückfall in {st.rest_s:.0f} s)"
            pw = f"{st.power_w:.1f}" if st and st.power_w == st.power_w else "–"
            tm = "–" if c.fail_on else (f"{c.totmann_s} s" + (" (nur erneuern)" if c.typ in NO_FALLBACK else ""))
            auto = []
            if c.auto_lauf:
                auto.append("Lauf")
            if c.auto_dauer:
                auto.append("Dauermessung")
            a = (" + ".join(auto) + f", Nachlauf {c.nachlauf_s:.0f} s") if auto else "–"
            pend = self.plugs.automation.pending_text(c.nr, now)
            if pend and auto:
                a += f" · {pend}"
            vals = [str(c.nr), c.name or "", c.typ, c.host, state, pw, tm,
                    "bleibt an" if c.fail_on else "fällt aus", a]
            for col, v in enumerate(vals):
                it = t.item(r, col)
                if it is None:
                    it = QtWidgets.QTableWidgetItem()
                    t.setItem(r, col, it)
                it.setText(v)
                if col == 4:
                    it.setBackground(QtGui.QColor("#c8e6c9" if st and st.on else "#ffcdd2"
                                                  if st and st.reachable is False else "#ffffff"))
            if c.nr == cur:
                t.selectRow(r)

    def automatic(self, lauf: bool, dauer: bool):
        """Aus dem Hauptfenster-Takt: Automatik der Steckdosen."""
        if self.plugs.automatic(lauf, dauer):
            self._fill_plugs()

    # ------------------------------------------------------------------ Takt
    def poll(self):
        self._check_link()
        self.bg.poll()
        if self.plugs.poll():
            self._fill_plugs()
        if self.plugs.messages:
            self.p_msg.setText(self.plugs.messages[-1])
            self.plugs.messages.clear()
            if self.plugs.found:
                self.p_msg.setText(self.p_msg.text() + ": " + ", ".join(
                    f"{f.get('typ') or '?'} {f.get('host')}" for f in self.plugs.found[:6]) +
                    " – „Hinzufügen …“ und Adresse wählen")
        now = time.time()
        link = self.link
        if link is not None and link.is_basis and link.ign_source == "zange" and now - self._last_zange > 2.0:
            self._last_zange = now
            self.bg.submit("zst", link.zangen_status, self._zange_result)
        if link is not None and link.is_basis and now - self._last_net > 10.0:
            self._last_net = now
            self._refresh_net()
        interval = 3.0 if self.isVisible() else 10.0
        if self.plugs.configs and now - self._last_plugs > interval and self.plugs.idle():
            if self.plugs.mode == "direkt" or (link is not None and link.is_basis):
                self._last_plugs = now
                self.plugs.refresh()

    def _zange_result(self, zangen, err):
        if err is not None or zangen is None:
            return
        st = main_zange(zangen)
        if st is None:
            return
        self.zst = st
        self._show_zange(st)
        weitere = [z for z in zangen if z is not st]
        self.z_weitere.setText("\n".join(f"ID {z.id}: " + z.summary().replace("Zange: ", "") for z in weitere))
        self.z_weitere.setVisible(bool(weitere))
        if weitere:
            self.zange_text.emit(st.summary() + f" (+{len(weitere)} weitere)")

    def close_page(self):
        self.plugs.close()
        for d in self.demo:
            try:
                d.close()
            except Exception:
                pass
