"""
Simulierte Messelektronik: verhaelt sich wie die PST-STM32-Firmware 1.0 (Arduino-Protokoll)
und simuliert Motor + Rolle, damit PyST ohne Pruefstand getestet werden kann.

Ablauf nach 'm': Motor haelt 3000 1/min, nach 3 s Vollgas bis 11500 1/min, dann Gas weg
und die Rolle rollt aus. Motor ~ 32 PS bei 10000 1/min (wie die Referenzlaeufe vom 26.05.26).
"""
import math
import random
import threading
import time
from typing import Optional


class SimEngine:
    def __init__(self, inkr=100.0, imp=1.0, ratio=6.85, inertia=13.5, t_max=23.0, n_peak=9500.0,
                 span=9000.0, n_hold=3000.0, n_lift=11500.0, rate=60.0, noise=0.002, seed=1,
                 loss0=0.5, loss2=0.0002):
        self.inkr, self.imp, self.ratio, self.inertia = inkr, imp, ratio, inertia
        self.t_max, self.n_peak, self.span = t_max, n_peak, span
        self.n_hold, self.n_lift, self.rate, self.noise = n_hold, n_lift, rate, noise
        self.loss0, self.loss2 = loss0, loss2
        self.rng = random.Random(seed)
        self.reset()

    def torque_wot(self, n_rpm: float) -> float:
        """Vollgas-Drehmoment an der Kurbelwelle [Nm] (Parabel um n_peak)."""
        x = (n_rpm - self.n_peak) / self.span
        t = self.t_max * (1.0 - x * x)
        if n_rpm < self.n_peak:
            t = max(t, 0.3 * self.t_max)                   # unten herum Mindestmoment
        return max(0.0, t)

    def reset(self):
        self.t = 0.0
        # Fahrer haelt beim Start schon die Drehzahl (wie im LabVIEW-Ablauf)
        self.w_roll = self.n_hold / 60.0 * 2 * math.pi / self.ratio
        self.phase = "hold"
        self.t_phase = 0.0

    def step(self, dt: float):
        n_e = self.w_roll * self.ratio * 60.0 / (2 * math.pi)
        loss_roll = self.loss0 + self.loss2 * self.w_roll ** 2   # Lager/Luft an der Rolle [Nm]
        if self.phase == "hold":
            # Drehzahl halten (P-Regler auf Motordrehzahl), nach 4 s Vollgas
            t_eng = min(self.torque_wot(n_e), max(0.0, 0.05 * (self.n_hold - n_e) + loss_roll / self.ratio))
            if self.t_phase > 3.0:
                self.phase, self.t_phase = "wot", 0.0
        elif self.phase == "wot":
            t_eng = self.torque_wot(n_e)
            if n_e >= self.n_lift:
                self.phase, self.t_phase = "coast", 0.0
        else:
            t_eng = -2.0 if n_e > 1500 else 0.0             # Motorbremse, Kupplung bei 1500 auf
            loss_roll += 15.0 if self.t_phase > 3.0 else 0.0  # Bremse nach 3 s
        m_roll = t_eng * self.ratio - loss_roll
        self.w_roll = max(0.0, self.w_roll + m_roll / self.inertia * dt)
        self.t += dt
        self.t_phase += dt

    def climate_line(self) -> str:
        return "21.5;96512;45.3"

    def next_frame(self):
        """-> (Dauer [s], Messfrequenz, f_Zuend, f_Rolle, EGT1, AFR, EGT2)"""
        f_ign, f_roll = self.frame()
        return 1.0 / self.rate, self.rate, f_ign, f_roll, 0.0, 0.0, 0.0

    def frame(self):
        """Ein Telegramm-Intervall integrieren, liefert (f_Zuend, f_Rolle) in Hz."""
        steps = 20
        dt = 1.0 / self.rate / steps
        acc = 0.0
        for _ in range(steps):
            self.step(dt)
            acc += self.w_roll
        w = acc / steps
        f_roll = w / (2 * math.pi) * self.inkr
        n_e = w * self.ratio * 60.0 / (2 * math.pi)
        if self.phase == "coast" and n_e < 1500:
            n_e = 1500.0                                      # Leerlauf, Kupplung offen
        f_ign = n_e / 60.0 * self.imp
        f_roll *= 1.0 + self.rng.gauss(0.0, self.noise)
        f_ign *= 1.0 + self.rng.gauss(0.0, self.noise * 2)
        return max(0.0, f_ign), max(0.0, f_roll)

class ReplaySource:
    """Spielt die Rohdaten eines gespeicherten Laufs (LabVIEW-XML oder PyST) Telegramm fuer
    Telegramm so ab, wie die Firmware sie gesendet haette."""

    def __init__(self, path: str):
        from . import storage
        self.run = storage.load_any(path)
        p = self.run["params"]
        self.params = p
        self.rate = 1.0 / max(1e-6, float(sum(self.run["dt"]) / len(self.run["dt"])))
        self.k = 0

    def reset(self):
        self.k = 0

    def climate_line(self) -> str:
        p = self.params
        return f"{p.temp_c:.1f};{p.p_mbar * 100.0:.0f};0.0"

    def next_frame(self):
        r, p, k = self.run, self.params, self.k
        if k >= len(r["dt"]):
            return 1.0 / self.rate, self.rate, 0.0, 0.0, 0.0, 0.0, 0.0   # Datei zu Ende: Stillstand
        self.k += 1
        dt = float(r["dt"][k]) or 1.0 / self.rate
        return (dt, 1.0 / dt, r["n_meas"][k] * p.imp / 60.0, r["n_roll"][k] * p.inkr / 60.0,
                float(r["egt"][k]), float(r["afr"][k]), 0.0)


class SimSerial:
    """Minimaler Ersatz fuer serial.Serial (write, read, in_waiting, close).
    Quelle: SimEngine (synthetischer Motor) oder ReplaySource (gespeicherter Lauf)."""

    def __init__(self, engine=None, realtime: bool = True, source=None, mega: bool = False):
        """mega=True: verhaelt sich wie der Arduino-Mega-Sketch 3.0.0 ('m' schaltet um, kein 's',
        'e' stoppt nicht, ganze Hz, AFR 20.00 ohne Sonde, kein Autostopp)."""
        self.engine = source or engine or SimEngine()
        self.mega = mega
        self.realtime = realtime
        self.buf = bytearray()
        self.lock = threading.Lock()
        self.streaming = False
        self.cycle = 0
        self.idle = 0
        self.active_seen = False
        self.open = True
        self.port = "SIM"
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    # --- serial.Serial-Schnittstelle
    @property
    def in_waiting(self) -> int:
        with self.lock:
            return len(self.buf)

    def read(self, n: int = 1) -> bytes:
        deadline = time.time() + 0.05
        while self.realtime and not self.in_waiting and time.time() < deadline:
            time.sleep(0.002)
        with self.lock:
            out = bytes(self.buf[:n])
            del self.buf[:n]
        return out

    def write(self, data: bytes) -> int:
        for c in data.decode("latin-1"):
            self._command(c)
        return len(data)

    def reset_input_buffer(self):
        with self.lock:
            self.buf.clear()

    def close(self):
        self.open = False

    # --- Firmware-Verhalten
    def _out(self, s: str):
        with self.lock:
            self.buf += s.encode("latin-1")

    def _command(self, c: str):
        if self.mega:
            if c == "e":
                self._out(self.engine.climate_line() + "\r\n")
            elif c == "m":
                if self.streaming:
                    self.streaming = False
                    self.cycle = 0
                else:
                    self.engine.reset()
                    self.streaming = True
            elif c == "v":
                self._out("Version 3.0.0 vom 28.03.24;\r\nRingspeicher Zuendsignal:5;\r\nRingspeicher Rolle:50;\r\n"
                          "Adresse BME280:76;\r\nThermoelement (1=ja 0=nein):0;\r\n")
            return
        if c == "e":
            self.streaming = False
            self._out(self.engine.climate_line() + "\r\n")
        elif c == "m":
            if not self.streaming:
                self.cycle = 0
                self.engine.reset()
                self.active_seen = False
                self.idle = 0
            self.streaming = True
        elif c == "s":
            self.streaming = False
        elif c in "v?":
            self._out(f"PST-STM32 SIMULATOR;\r\nMessfrequenz [Hz]:{self.engine.rate:.0f};\r\n")

    def tick(self) -> float:
        """Ein Telegramm erzeugen. Liefert dessen Dauer [s] (fuer die Echtzeit-Taktung)."""
        if not self.streaming:
            return 0.05
        dt, rate, f_ign, f_roll, egt1, afr, egt2 = self.engine.next_frame()
        self.cycle = (self.cycle + 1) & 0xFFFF
        if self.mega:
            # Mega 3.0.0 ohne Thermoelement: cycle;timer;f_Zuend;f_Rolle;Temp_Sonde;AFR (ganze Hz, AFR 20.00 ohne Sonde)
            self._out(f"{self.cycle};{rate:.2f};{int(f_ign)};{int(f_roll)};0;20.00\n")
            return dt
        self._out(f"{self.cycle};{rate:.2f};{f_ign:.2f};{f_roll:.2f};{egt1:.0f};{afr:.2f};{egt2:.0f}\n")
        if f_ign > 0 or f_roll > 0:
            self.active_seen, self.idle = True, 0
        elif self.active_seen:
            self.idle += 1
            if self.idle >= 5 * self.engine.rate:
                self.streaming = False
        return dt

    def _run(self):
        nxt = time.time()
        while self.open:
            if self.realtime:
                was_streaming = self.streaming
                dt = self.tick()
                if not was_streaming:
                    nxt = time.time()
                nxt += dt
                time.sleep(max(0.0, nxt - time.time()))
            else:
                time.sleep(0.1)
