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
