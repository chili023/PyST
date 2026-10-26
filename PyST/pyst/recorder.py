"""
Dauermessung: unbegrenzt lange Aufzeichnung aller Telegramme, unabhaengig vom Leistungslauf.

Ordner  messungen/YYMMDD_HHMMSS_Dauermessung/
  dauer.json        Kopfdaten (waehrend der Messung regelmaessig neu geschrieben)
  teil_0001.csv …   Telegramme in Abschnitten (je ABSCHNITT_S Sekunden eine Datei), alle FLUSH_S Sekunden
                    auf die Platte geschrieben (fsync) -> ein Absturz verliert hoechstens wenige Sekunden
  marker.csv        Marker mit Text ("Kabel bewegt"), sofort geschrieben
  ereignisse.csv    erkannte Ereignisse (Software-Trigger)
  bursts/burst_NNN.csv  Flankenaufnahmen (PST-Diag 1)
Beim Beenden: Abschnitte -> roh.csv, dauer.json -> lauf.json (art = "dauermessung"). Nach einem Absturz
laedt storage.load_any den Ordner trotzdem (aus den Abschnitten).
"""
import csv
import datetime as _dt
import json
import os
import time
from dataclasses import asdict
from typing import List, Optional, Tuple

from . import __version__, physics
from .storage import DIAG_HEADER, ROH_HEADER, burst_to_csv, frame_row

ABSCHNITT_S = 60.0
FLUSH_S = 2.0


class Recorder:
    def __init__(self, base_dir: str, params: physics.DynoParams, port: str = "", firmware: str = "",
                 diag: bool = False, vehicle: str = ""):
        stamp = _dt.datetime.now()
        self.folder = os.path.join(base_dir, stamp.strftime("%y%m%d_%H%M%S") + "_Dauermessung")
        os.makedirs(self.folder, exist_ok=True)
        self.params = params
        self.diag = diag
        self.t0: Optional[float] = None
        self.n = 0
        self.part = 0
        self.part_start = 0.0
        self._fh = None
        self._w = None
        self._last_flush = 0.0
        self.markers: List[Tuple[float, str]] = []
        self.events = 0
        self.bursts = 0
        self.meta = {
            "programm": f"PyST {__version__}", "art": "dauermessung", "datum": stamp.isoformat(timespec="seconds"),
            "fahrzeug": vehicle, "firmware": firmware, "port": port, "parameter": asdict(params),
            "klima": None, "com_verloren": 0, "telegramme": 0, "ergebnis": None, "diagnose": diag,
            "abschnitt_s": ABSCHNITT_S, "beendet": None,
        }
        self._write_meta("dauer.json")
        self.closed = False

    # ------------------------------------------------------------------ Schreiben
    def _write_meta(self, name: str):
        tmp = os.path.join(self.folder, name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.meta, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, os.path.join(self.folder, name))

    def _new_part(self, t: float):
        self._close_part()
        self.part += 1
        self.part_start = t
        self._fh = open(os.path.join(self.folder, f"teil_{self.part:04d}.csv"), "w", newline="")
        self._w = csv.writer(self._fh, delimiter=";")
        self._w.writerow(ROH_HEADER + (DIAG_HEADER if self.diag else []))

    def _close_part(self):
        if self._fh:
            self._fh.flush()
            os.fsync(self._fh.fileno())
            self._fh.close()
            self._fh = None

    def add_frames(self, frames, lost: int = 0):
        if self.closed or not frames:
            return
        if self.t0 is None:
            self.t0 = frames[0].t
        for f in frames:
            rel = f.t - self.t0
            if self._fh is None or rel - self.part_start >= ABSCHNITT_S:
                self._new_part(rel)
            self._w.writerow(frame_row(f, self.t0, self.params, self.diag))
            self.n += 1
        self.meta["com_verloren"] = lost
        now = time.time()
        if now - self._last_flush >= FLUSH_S:
            self._last_flush = now
            self._fh.flush()
            os.fsync(self._fh.fileno())
            self.meta["telegramme"] = self.n
            self._write_meta("dauer.json")

    def _append(self, name: str, header: List[str], row: List):
        path = os.path.join(self.folder, name)
        new = not os.path.exists(path)
        with open(path, "a", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh, delimiter=";")
            if new:
                w.writerow(header)
            w.writerow(row)
            fh.flush()
            os.fsync(fh.fileno())

    def rel_time(self, t_pc: Optional[float] = None) -> float:
        t_pc = time.time() if t_pc is None else t_pc
        return 0.0 if self.t0 is None else t_pc - self.t0

    def add_marker(self, text: str, t_rel: Optional[float] = None) -> float:
        t = self.rel_time() if t_rel is None else t_rel
        self.markers.append((t, text))
        self._append("marker.csv", ["zeit_s", "text"], [f"{t:.3f}", text])
        return t

    def add_event(self, ev):
        self.events += 1
        self._append("ereignisse.csv", ["zeit_s", "art", "text"], [f"{ev.t:.3f}", ev.kind, ev.text])

    def add_burst(self, burst) -> str:
        self.bursts += 1
        os.makedirs(os.path.join(self.folder, "bursts"), exist_ok=True)
        path = os.path.join(self.folder, "bursts", f"burst_{self.bursts:03d}.csv")
        burst.request.setdefault("zeit_s", f"{self.rel_time(burst.t_pc):.3f}")
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(burst_to_csv(burst))
        return path

    def size_bytes(self) -> int:
        total = 0
        for root, _d, files in os.walk(self.folder):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
        return total

    # ------------------------------------------------------------------ Beenden
    def close(self) -> str:
        """Abschnitte zu roh.csv zusammenfassen, lauf.json schreiben. Liefert den Ordner."""
        if self.closed:
            return self.folder
        self.closed = True
        self._close_part()
        parts = sorted(f for f in os.listdir(self.folder) if f.startswith("teil_") and f.endswith(".csv"))
        roh = os.path.join(self.folder, "roh.csv")
        with open(roh + ".tmp", "w", newline="") as out:
            if not parts:
                csv.writer(out, delimiter=";").writerow(ROH_HEADER + (DIAG_HEADER if self.diag else []))
            for k, name in enumerate(parts):
                with open(os.path.join(self.folder, name), newline="") as fh:
                    lines = fh.read().splitlines(True)
                out.writelines(lines if k == 0 else lines[1:])
            out.flush()
            os.fsync(out.fileno())
        os.replace(roh + ".tmp", roh)
        for name in parts:
            os.remove(os.path.join(self.folder, name))
        self.meta.update(telegramme=self.n, beendet=_dt.datetime.now().isoformat(timespec="seconds"),
                         marker=len(self.markers), ereignisse=self.events, bursts=self.bursts)
        self._write_meta("lauf.json")
        try:
            os.remove(os.path.join(self.folder, "dauer.json"))
        except OSError:
            pass
        return self.folder
