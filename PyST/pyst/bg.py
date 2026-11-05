"""
Arbeit neben der Oberflaeche.

- Bg: ein Hintergrund-Thread fuer Befehle an die Basis, Ergebnisse holt poll() im GUI-Takt ab.
- wait_in_thread(): fuer Aufrufer, die das Ergebnis sofort brauchen. Die Arbeit laeuft in einem Thread, die Oberflaeche
  zeichnet weiter, Eingaben warten. Die Takte des Hauptfensters pausieren solange (waiting()), damit nichts die laufende
  Arbeit unterbricht, etwa ein Trennen unter dem Lesevorgang.
"""
import queue
import threading
from typing import Callable, Optional

from PySide6 import QtCore

_waits = 0


class Bg:
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


def waiting() -> bool:
    """Eine Oberflaechen-Aktion wartet gerade auf ihren Thread."""
    return _waits > 0


def wait_in_thread(fn: Callable, tick: Optional[Callable[[], None]] = None):
    """fn() im Thread ausfuehren und das Ergebnis liefern, Ausnahmen kommen beim Aufrufer an.
    tick() laeuft waehrenddessen im GUI-Thread (Fortschritt anzeigen)."""
    global _waits
    out: dict = {}
    fertig = threading.Event()

    def work():
        try:
            out["res"] = fn()
        except BaseException as exc:
            out["err"] = exc
        finally:
            fertig.set()

    loop = QtCore.QEventLoop()
    timer = QtCore.QTimer()
    timer.setInterval(5)

    def check():
        if tick is not None:
            tick()
        if fertig.is_set():
            loop.quit()

    timer.timeout.connect(check)
    _waits += 1
    try:
        threading.Thread(target=work, daemon=True).start()
        timer.start()
        loop.exec(QtCore.QEventLoop.ExcludeUserInputEvents)
    finally:
        timer.stop()
        _waits -= 1
    if "err" in out:
        raise out["err"]
    return out.get("res")
