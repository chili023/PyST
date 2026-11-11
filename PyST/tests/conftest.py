"""Tests laufen mit eigenem Benutzerordner: ~/PyST mit Einstellungen, Datenbank und Schluesseln bleibt unberuehrt."""
import atexit
import os
import shutil
import sys
import tempfile

import pytest

_home = tempfile.mkdtemp(prefix="pyst-tests-")
atexit.register(shutil.rmtree, _home, ignore_errors=True)
os.environ["HOME"] = os.environ["USERPROFILE"] = _home

# Echte Geraete am Testrechner (Mega, Nucleo, Basis, Zange) sehen und oeffnen die Tests nie: sonst verbindet jedes
# Hauptfenster sie per Auto-Verbinden und schickt Befehle (10.11.2026: F446 am TUF waehrend der Suite offen). Tests,
# die Ports brauchen, setzen eigene comports-Listen bzw. den Simulator ein.
import serial  # noqa: E402
from serial.tools import list_ports as _list_ports  # noqa: E402

_list_ports.comports = lambda *_a, **_k: []
ECHTE_PORTS = ("/dev/ttyACM", "/dev/ttyUSB", "/dev/ttyS", "/dev/cu.", "/dev/tty.", "COM")
_serial_open = serial.Serial.open


def _nur_testports(self):
    port = str(self.port or "")
    if port.startswith(ECHTE_PORTS):
        raise serial.SerialException(f"Tests öffnen keinen echten Port ({port})")
    return _serial_open(self)


serial.Serial.open = _nur_testports


@pytest.fixture(autouse=True)
def _start_nachfrage_ohne_fenster(monkeypatch):
    """START fragt bei unveraendertem Setup modal nach (messen_page.StartNachfrage); ein Test, der START drueckt, bliebe
    dort haengen. In den Tests drueckt die Nachfrage sofort „Unverändert starten“; test_messen prueft das Fenster."""
    mp = sys.modules.get("pyst.messen_page")
    if mp is not None and hasattr(mp, "StartNachfrage"):
        def exec_(dlg):
            dlg.b_start.click()
            return dlg.result()
        monkeypatch.setattr(mp.StartNachfrage, "exec", exec_)


def close_window(w, app):
    """Hauptfenster wirklich beenden: unittest haelt die Testobjekte bis zum Schluss, die Takte eines nur geschlossenen
    Fensters liefen in allen folgenden Oberflaechen-Tests weiter und bremsten sie."""
    from PySide6 import QtCore
    if w.link is not None:
        w.on_connect()
    w.close()
    for t in w.findChildren(QtCore.QTimer):
        t.stop()
    app.processEvents()
    w.deleteLater()
    app.processEvents()
