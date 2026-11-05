"""Tests laufen mit eigenem Benutzerordner: ~/PyST mit Einstellungen, Datenbank und Schluesseln bleibt unberuehrt."""
import atexit
import os
import shutil
import tempfile

_home = tempfile.mkdtemp(prefix="pyst-tests-")
atexit.register(shutil.rmtree, _home, ignore_errors=True)
os.environ["HOME"] = os.environ["USERPROFILE"] = _home


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
