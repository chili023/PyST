"""
Simulierte Messelektronik: verhaelt sich wie die PST-STM32-Firmware 1.1 bzw. der Arduino-Mega-Sketch
3.1.0 (Arduino-Protokoll + PST-Diag 1) oder – ohne Diagnose – wie F446 1.0.0 / Mega 3.0.0,
und simuliert Motor + Rolle, damit PyST ohne Pruefstand getestet werden kann.

Ablauf nach 'm': Motor haelt 3000 1/min, nach 3 s Vollgas bis 11500 1/min, dann Gas weg
und die Rolle rollt aus. Motor ~ 32 PS bei 10000 1/min (wie die Referenzlaeufe vom 26.05.26).

PST-Diag 1: '#?', '#MODE', '#RUN', '#RATE', '#BURST' (mit Trigger), '#GET/#SET/#SAVE/#DEFAULTS'.
Dafuer erzeugt der Simulator die einzelnen Flanken von Zuendung und Rolle (mit uint32-Zeitstempeln,
die bald ueberlaufen) und kann Stoerungen einstreuen (Doppelimpuls und Aussetzer der Zuendung,
Rollenspruenge, Wackelkontakt). Die Stoerungen sind als Einstellungen sim_* einstellbar
(#SET bzw. Dialog „Geräte-Einstellungen“) – Werte = Ereignisse pro Minute.
"""
import bisect
import math
import random
import threading
import time
from collections import deque
from typing import Dict, List, Optional


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


# Stoerungen je Minute fuer die Simulator-Ports ":stoerung"
FAULT_PRESET = {"sim_doppel": 6.0, "sim_aussetzer": 4.0, "sim_rollensprung": 8.0, "sim_wackel": 3.0}

SETTINGS_STM = {"edge_ign": "F", "edge_roll": "R", "timeout_ign_ms": "500", "timeout_roll_ms": "500",
                "pullup": "1", "thermo": "0", "ring_ign": "5", "ring_roll": "50", "ign_lockout_pct": "0",
                "ign_min_period_us": "0", "roll_min_period_us": "0", "rate_hz": "60"}
# Mega 3.1.0 (docs/04_PST_Diag_1.md §9): kein rate_hz, bme_addr als 0x76, afr_addr
SETTINGS_MEGA = {"edge_ign": "F", "edge_roll": "F", "timeout_ign_ms": "500", "timeout_roll_ms": "1000",
                 "pullup": "1", "thermo": "0", "bme_addr": "0x76", "afr_addr": "1", "ring_ign": "5", "ring_roll": "50",
                 "ign_lockout_pct": "0", "ign_min_period_us": "0", "roll_min_period_us": "0"}
SIM_KEYS = ("sim_doppel", "sim_aussetzer", "sim_rollensprung", "sim_wackel")


class EdgeSim:
    """Erzeugt die Flanken beider Kanaele aus den Frequenzen eines Telegramm-Intervalls und streut
    Stoerungen ein. Zeit in Sekunden ab Start des Simulators."""

    def __init__(self, rng: random.Random):
        self.rng = rng
        self.next_ign = 0.0         # Zeitpunkt der naechsten Zuend-Flanke (fallend)
        self.next_roll = 0.0        # naechste Rollen-Flanke (Halbperiode)
        self.roll_lvl = 0
        self.episode: Dict[str, int] = {}       # Stoerung -> verbleibende Telegramme
        self.roll_mult = 1
        self.pending_clean: list = []
        self.pending_dist: list = []

    def faults_step(self, rates: Dict[str, float], dt: float):
        """Neue Stoerungs-Episoden auswuerfeln (rates in Ereignisse/Minute)."""
        for k in list(self.episode):
            self.episode[k] -= 1
            if self.episode[k] <= 0:
                del self.episode[k]
        lengths = {"sim_doppel": (3, 8), "sim_aussetzer": (2, 6), "sim_rollensprung": (1, 3), "sim_wackel": (10, 40)}
        for k, (lo, hi) in lengths.items():
            r = float(rates.get(k, 0.0) or 0.0)
            if r > 0 and k not in self.episode and self.rng.random() < r / 60.0 * dt:
                self.episode[k] = self.rng.randint(lo, hi)
                if k == "sim_rollensprung":
                    self.roll_mult = self.rng.choice([2, 3, 4, 6])

    def generate(self, t0: float, t1: float, f_ign: float, f_roll: float):
        """-> (sauber, gestoert): Listen (t, Kanal, Pegel) zeitlich sortiert."""
        clean, dist = [], []
        ep = self.episode
        # Zuendung: Low-Puls je Zuendfunke (Optokoppler, Ruhepegel high)
        if f_ign > 0.5:
            T = 1.0 / f_ign
            w = min(0.3 * T, 0.001)
            if self.next_ign < t0 - T:
                self.next_ign = t0
            while self.next_ign < t1:
                tp = self.next_ign
                clean += [(tp, "I", 0), (tp + w, "I", 1)]
                drop = "sim_aussetzer" in ep and self.rng.random() < 0.7
                if not drop:
                    dist += [(tp, "I", 0), (tp + w, "I", 1)]
                    if "sim_doppel" in ep:                   # Nachschwinger: zweiter Impuls kurz danach
                        t2 = tp + 0.22 * T
                        dist += [(t2, "I", 0), (t2 + w * 0.5, "I", 1)]
                self.next_ign += T
        else:
            self.next_ign = t1
        # Rolle: Rechteck 50 %
        if f_roll > 0.5:
            H = 0.5 / f_roll
            if self.next_roll < t0 - 2 * H:
                self.next_roll = t0
            while self.next_roll < t1:
                te = self.next_roll
                self.roll_lvl ^= 1
                e = (te, "R", self.roll_lvl)
                clean.append(e)
                if "sim_wackel" in ep and self.rng.random() < 0.35:
                    pass                                     # Flanke geht verloren
                else:
                    dist.append(e)
                glitches = 0
                if "sim_rollensprung" in ep and self.roll_lvl == 1:
                    glitches = self.roll_mult - 1
                if "sim_wackel" in ep and self.rng.random() < 0.3:
                    glitches += self.rng.randint(1, 3)
                for g in range(glitches):                    # Stoerimpulse (2 µs) innerhalb der Periode
                    tg = te + (g + 1) * 2 * H / (glitches + 1)
                    lv = self.roll_lvl if tg - te < H else 1 - self.roll_lvl     # Pegel zu diesem Zeitpunkt
                    dist += [(tg - 2e-6, "R", 1 - lv), (tg, "R", lv)]
                self.next_roll += H
        else:
            self.next_roll = t1
        # Flanken nach dem Intervallende (z.B. Ende des Zuendimpulses) erst mit dem naechsten Intervall ausgeben,
        # damit alle Flanken zeitlich aufsteigend bleiben
        clean = sorted(clean + self.pending_clean)
        dist = sorted(dist + self.pending_dist)
        k1 = bisect.bisect_left(clean, (t1,))
        k2 = bisect.bisect_left(dist, (t1,))
        self.pending_clean, self.pending_dist = clean[k1:], dist[k2:]
        return clean[:k1], dist[:k2]


class ChannelState:
    """Zeitstempel der gueltigen Flanken eines Kanals (sauber und gestoert) fuer die Frequenzberechnung."""

    def __init__(self):
        self.clean: deque = deque(maxlen=400)
        self.dist: deque = deque(maxlen=400)
        self.win: List[float] = []          # gueltige gestoerte Flanken im aktuellen Telegramm-Fenster
        self.periods: List[float] = []
        self.rejected = 0
        self.last_period = 0.0
        self.started = 0.0                  # Beginn der Flankenerzeugung [s]

    def reset(self, t: float):
        self.clean.clear()
        self.dist.clear()
        self.last_period = 0.0
        self.started = t

    @staticmethod
    def ring_freq(ts, k: int) -> float:
        if len(ts) < 2:
            return 0.0
        k = max(1, min(k, len(ts) - 1))
        dt = ts[-1] - ts[-1 - k]
        return k / dt if dt > 0 else 0.0


class SimSerial:
    """Minimaler Ersatz fuer serial.Serial (write, read, in_waiting, close).
    Quelle: SimEngine (synthetischer Motor) oder ReplaySource (gespeicherter Lauf)."""

    def __init__(self, engine=None, realtime: bool = True, source=None, mega: bool = False, diag: bool = True,
                 faults: Optional[Dict[str, float]] = None, seed: int = 7):
        """mega=True: verhaelt sich wie der Arduino-Mega-Sketch ('m' schaltet um, kein 's',
        'e' stoppt nicht, ganze Hz, AFR 20.00 ohne Sonde, kein Autostopp).
        diag=False: altes Geraet ohne PST-Diag 1 (Mega 3.0.0 / F446 1.0.0), '#'-Zeilen werden ignoriert.
        faults: Stoerungen je Minute (sim_doppel, sim_aussetzer, sim_rollensprung, sim_wackel)."""
        self.engine = source or engine or SimEngine()
        self.mega = mega
        self.diag = diag
        self.realtime = realtime
        self.buf = bytearray()
        self.lock = threading.Lock()
        self.streaming = False
        self.cycle = 0
        self.idle = 0
        self.active_seen = False
        self.open = True
        self.port = "SIM"
        # PST-Diag 1
        self.tick_ns = 62.5 if mega else 11.111
        self.defaults = dict(SETTINGS_MEGA if mega else SETTINGS_STM)
        self.defaults.update({k: "0" for k in SIM_KEYS})
        self.settings = dict(self.defaults)
        for k, v in (faults or {}).items():
            self.settings[k] = f"{v:g}"
        self.saved: Optional[Dict[str, str]] = None
        self.mode = "N"
        self.line: Optional[str] = None             # '#'-Zeile im Empfang
        self.t_sim = 0.0
        # Zeitstempel starten kurz vor dem uint32-Ueberlauf (Test der Entfaltung in PyST)
        self.tick_offset = (1 << 32) - int(2.0e9 / self.tick_ns)
        self.ms_offset = (1 << 32) - 1500
        self.rng = random.Random(seed)
        self.edges = EdgeSim(self.rng)
        self.ch = {"I": ChannelState(), "R": ChannelState()}
        self.burst: Optional[dict] = None
        self.flags_extra = 0
        self.last_out = {"I": 0.0, "R": 0.0}       # alter Mega: letzter Wert bleibt stehen
        self._edges_on = False
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
            if self.line is not None:
                if c in "\r\n":
                    line, self.line = self.line, None
                    self._diag_command(line)
                elif len(self.line) < 63:
                    self.line += c
                continue
            if c == "#" and self.diag:
                self.line = "#"                     # Zeichen der '#'-Zeile nicht als Einzelbefehle werten
                continue
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

    def _start(self):
        if not self.streaming:
            self.cycle = 0
            self.engine.reset()
            self.active_seen = False
            self.idle = 0
        self.streaming = True

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
                ver = "3.1.0 vom 26.10.26" if self.diag else "3.0.0 vom 28.03.24"
                self._out(f"Version {ver};\r\nRingspeicher Zuendsignal:5;\r\nRingspeicher Rolle:50;\r\n"
                          "Adresse BME280:76;\r\nThermoelement (1=ja 0=nein):0;\r\n")
            return
        if c == "e":
            self.streaming = False
            self._out(self.engine.climate_line() + "\r\n")
        elif c == "m":
            self._start()
        elif c == "s":
            self.streaming = False
        elif c in "v?":
            fw = "1.1.0" if self.diag else "1.0.0"
            self._out(f"PST-STM32 SIMULATOR {fw};\r\nMessfrequenz [Hz]:{self.engine.rate:.0f};\r\n")

    def _diag_command(self, line: str):
        """PST-Diag 1: eine '#'-Zeile auswerten."""
        parts = line[1:].strip().split()
        cmd = parts[0].upper() if parts else ""
        a = parts[1:]
        if cmd == "?":
            fw = "PST-Mega 3.1.0 SIM" if self.mega else "PST-STM32 1.1.0 SIM"
            self._out(f"#CAP;proto=1;fw={fw};tick_ns={self.tick_ns:g};ch=I,R;burst_max={self.burst_max()};"
                      f"modes=N,D;set=1\r\n")
        elif cmd == "MODE" and a and a[0].upper() in ("N", "D"):
            self.mode = a[0].upper()
            self._out(f"#OK;MODE;{self.mode}\r\n")
        elif cmd == "RUN" and a and a[0] in ("0", "1"):
            if a[0] == "1":
                self._start()
            else:
                self.streaming = False
                self.cycle = 0 if self.mega else self.cycle
            self._out(f"#OK;RUN;{a[0]}\r\n")
        elif cmd == "RATE" and a:
            try:
                hz = float(a[0])
            except ValueError:
                hz = 0
            if self.mega or not isinstance(self.engine, SimEngine):
                self._out("#ERR;RATE;nicht unterstuetzt\r\n")
            elif 10 <= hz <= 100:
                self.engine.rate = hz
                self.settings["rate_hz"] = f"{hz:g}"
                self._out(f"#OK;RATE;{hz:g}\r\n")
            else:
                self._out("#ERR;RATE;bereich 10-100\r\n")
        elif cmd == "BURST":
            self._burst_command(a)
        elif cmd == "GET":
            for k, v in self.settings.items():
                self._out(f"#SET;{k}={v}\r\n")
            self._out("#OK;GET\r\n")
        elif cmd == "SET" and len(a) >= 2:
            k, v = a[0], a[1]
            if k not in self.settings:
                self._out(f"#ERR;SET;unbekannt {k}\r\n")
                return
            if k in ("edge_ign", "edge_roll") and v.upper() not in ("F", "R"):
                self._out("#ERR;SET;wert\r\n")
                return
            if k == "bme_addr":
                v = "0x" + v[2:] if v.lower().startswith("0x") else "0x" + v
            elif k not in ("edge_ign", "edge_roll"):
                try:
                    float(v)
                except ValueError:
                    self._out("#ERR;SET;wert\r\n")
                    return
            self.settings[k] = v.upper() if k.startswith("edge") else v
            if k == "rate_hz" and isinstance(self.engine, SimEngine):
                self.engine.rate = max(10.0, min(100.0, float(v)))
            self._out(f"#OK;SET;{k}={self.settings[k]}\r\n")
        elif cmd == "SAVE" and (self.streaming or self.burst is not None):
            self._out("#ERR;SAVE;messung laeuft\r\n")
        elif cmd == "SAVE":
            self.saved = dict(self.settings)
            self._out("#OK;SAVE\r\n")
        elif cmd == "DEFAULTS":
            self.settings = dict(self.defaults)
            self._out("#OK;DEFAULTS\r\n")
        else:
            self._out("#ERR;?;unbekannt\r\n")

    def burst_max(self) -> int:
        return 800 if self.mega else 8192

    def _burst_command(self, a: List[str]):
        if a and a[0].upper() == "STOP":
            self.burst = None
            self._out("#OK;BURST;STOP\r\n")
            return
        try:
            ch, n, edge = a[0].upper(), int(a[1]), a[2].upper()
        except (IndexError, ValueError):
            self._out("#ERR;BURST;parameter\r\n")
            return
        if ch not in ("I", "R", "A") or edge not in ("F", "R", "B") or not 1 <= n <= self.burst_max():
            self._out("#ERR;BURST;parameter\r\n")
            return
        trig, pre = None, None
        for x in a[3:]:
            try:
                if x[:1].upper() == "T":
                    trig = float(x[1:])
                elif x[:1].upper() == "P":
                    pre = int(x[1:])
            except ValueError:
                self._out("#ERR;BURST;parameter\r\n")
                return
        if pre is None:
            pre = n // 2
        pre = max(0, min(pre, n - 1)) if trig else 0
        self.burst = {"ch": ch, "n": n, "edge": edge, "trig": trig, "pre": pre, "buf": deque(maxlen=pre),
                      "post": [], "trig_idx": -1, "hist": {}}
        self._out(f"#OK;BURST;ARMED;ch={ch};n={n};edge={edge};trig={trig if trig else 0:g};pre={pre}\r\n")

    def _ticks(self, t: float) -> int:
        return (int(round(t * 1e9 / self.tick_ns)) + self.tick_offset) & 0xFFFFFFFF

    def _burst_edge(self, e):
        """Eine (gestoerte) Flanke in die laufende Burst-Aufnahme geben."""
        b = self.burst
        t, ch, lvl = e
        if b["ch"] != "A" and ch != b["ch"]:
            return
        if b["edge"] != "B" and lvl != (0 if b["edge"] == "F" else 1):
            return
        if b["trig_idx"] >= 0 or not b["trig"]:
            b["post"].append(e)
        else:
            # Trigger: Periode weicht mehr als trig % vom Mittel der 8 vorigen Perioden ab (gleicher Kanal/Richtung)
            h = b["hist"].setdefault((ch, lvl), deque(maxlen=10))
            fired = False
            if len(h) >= 9:
                per = [h[i + 1] - h[i] for i in range(len(h) - 1)][-8:]
                mean = sum(per) / len(per)
                p = t - h[-1]
                # erst ausloesen, wenn der Vorlauf voll ist (trig_idx = pre wie in der Firmware)
                fired = mean > 0 and abs(p - mean) / mean * 100.0 > b["trig"] and len(b["buf"]) >= b["pre"]
            h.append(t)
            if fired:
                b["trig_idx"] = len(b["buf"])
                b["post"] = list(b["buf"]) + [e]
            else:
                b["buf"].append(e)
        if len(b["post"]) >= b["n"]:
            self._burst_output()

    def _burst_output(self):
        b = self.burst
        self.burst = None
        edges = b["post"][:b["n"]]
        self._out(f"#BURST;BEGIN;n={len(edges)};tick_ns={self.tick_ns:g};trig_idx={b['trig_idx']};ch={b['ch']}\r\n")
        self._out("".join(f"B;{i};{ch};{lvl};{self._ticks(t)}\r\n" for i, (t, ch, lvl) in enumerate(edges)))
        self._out(f"#BURST;END;n={len(edges)};lost=0\r\n")
        self.flags_extra |= 1 << 6

    def _valid(self, st: ChannelState, ch: str, lvl: int, t: float) -> bool:
        """Flanke der eingestellten Richtung? Firmware-Filter (Sperrzeit/Doppelimpulsfilter) anwenden."""
        want = 0 if self.settings["edge_ign" if ch == "I" else "edge_roll"] == "F" else 1
        if lvl != want:
            return False
        if st.dist:
            p = t - st.dist[-1]
            min_us = float(self.settings["ign_min_period_us" if ch == "I" else "roll_min_period_us"] or 0)
            lock = float(self.settings["ign_lockout_pct"] or 0) if ch == "I" else 0.0
            if p * 1e6 < min_us or (lock > 0 and st.last_period > 0 and p < (1 - lock / 100.0) * st.last_period):
                st.rejected += 1
                return False
            st.last_period = p
        return True

    def _edge_step(self, dt: float, f_ign: float, f_roll: float):
        """Flanken dieses Telegramm-Intervalls -> (Faktor Zuendung, Faktor Rolle) fuer die Stoerungen
        und Fensterstatistik fuer das D-Telegramm."""
        rates = {k: float(self.settings.get(k, 0) or 0) for k in SIM_KEYS}
        t0, t1 = self.t_sim, self.t_sim + dt
        self.edges.faults_step(rates, dt)
        clean, dist = self.edges.generate(t0, t1, f_ign, f_roll)
        for st in self.ch.values():
            st.win, st.periods = [], []
        for t, ch, lvl in clean:
            want = 0 if self.settings["edge_ign" if ch == "I" else "edge_roll"] == "F" else 1
            if lvl == want:
                self.ch[ch].clean.append(t)
        for e in dist:
            t, ch, lvl = e
            st = self.ch[ch]
            if self._valid(st, ch, lvl, t):
                if st.dist:
                    st.periods.append(t - st.dist[-1])
                st.dist.append(t)
                st.win.append(t)
            if self.burst is not None:
                self._burst_edge(e)
        factors = {}
        for ch, f_eng in (("I", f_ign), ("R", f_roll)):
            st = self.ch[ch]
            k = int(float(self.settings["ring_ign" if ch == "I" else "ring_roll"] or 1))
            timeout = float(self.settings["timeout_ign_ms" if ch == "I" else "timeout_roll_ms"] or 500) / 1000.0
            fc, fd = st.ring_freq(st.clean, k), st.ring_freq(st.dist, k)
            if f_eng <= 0:
                factors[ch] = 1.0
            elif (st.dist and t1 - st.dist[-1] > timeout) or (not st.dist and t1 - st.started > timeout):
                factors[ch] = None                           # keine Flanke mehr: Timeout
            elif fc > 0 and fd > 0:
                factors[ch] = fd / fc
            else:
                factors[ch] = 1.0
        return factors

    # --- Erweiterungspunkte (Basis/Zange, siehe BasisSimSerial)
    def _background(self):
        """Wird auch ohne laufende Ausgabe regelmaessig aufgerufen (Uhren, Steckdosen, Funk)."""

    def _extra_flags(self) -> int:
        return 0

    def _diag_tail(self) -> str:
        """Angehaengte Felder des D-Telegramms (';…'), Standard: keine."""
        return ""

    def _frame_hook(self, f_ign: float, f_roll: float, dt: float):
        """Werte des Telegramm-Intervalls vor der Ausgabe (Basis: Funkverlust, Zange weg)."""
        return f_ign, f_roll

    def tick(self) -> float:
        """Ein Telegramm erzeugen. Liefert dessen Dauer [s] (fuer die Echtzeit-Taktung)."""
        self._background()
        if not self.streaming:
            return 0.05
        dt, rate, f_ign, f_roll, egt1, afr, egt2 = self.engine.next_frame()
        f_ign, f_roll = self._frame_hook(f_ign, f_roll, dt)
        self.cycle = (self.cycle + 1) & 0xFFFF
        faults_on = any(float(self.settings.get(k, 0) or 0) > 0 for k in SIM_KEYS)
        flags = self.flags_extra | self._extra_flags()
        self.flags_extra = 0
        if faults_on or self.mode == "D" or self.burst is not None or self.edges.episode:
            if not self._edges_on:                           # Flankenerzeugung (wieder) aufnehmen
                for st in self.ch.values():
                    st.reset(self.t_sim)
                self._edges_on = True
            fac = self._edge_step(dt, f_ign, f_roll)
            vals = {}
            for ch, f in (("I", f_ign), ("R", f_roll)):
                if fac[ch] is None:
                    if self.diag or not self.mega:
                        vals[ch] = 0.0
                        flags |= 1 if ch == "I" else 2
                    else:
                        vals[ch] = self.last_out[ch]         # alter Mega: letzter Wert bleibt stehen
                else:
                    vals[ch] = f * fac[ch]
                if not self.ch[ch].win and f > 0 and fac[ch] is not None:
                    flags |= 1 << (4 if ch == "I" else 5)    # keine neue Periode im Fenster
            f_ign, f_roll = vals["I"], vals["R"]
        else:
            self._edges_on = False
            for st in self.ch.values():
                st.win, st.periods = [], []
        self.t_sim += dt
        self.last_out = {"I": f_ign, "R": f_roll}
        if self.burst is not None:
            flags |= 1 << 3
        if self.mode == "D" and self.diag:
            t_ms = (int(self.t_sim * 1000) + self.ms_offset) & 0xFFFFFFFF
            st_i, st_r = self.ch["I"], self.ch["R"]

            def stats(st):
                if not st.periods:
                    return "0.0;0.0"
                return f"{min(st.periods) * 1e6:.1f};{max(st.periods) * 1e6:.1f}"
            rej_i, rej_r = st_i.rejected, st_r.rejected
            st_i.rejected = st_r.rejected = 0
            nan = lambda x: "nan" if x != x else f"{x:.0f}"
            self._out(f"D;{self.cycle};{t_ms};{rate:.2f};{f_ign:.3f};{f_roll:.3f};{nan(egt1)};{afr:.2f};{nan(egt2)};"
                      f"{len(st_i.win)};{stats(st_i)};{rej_i};{len(st_r.win)};{stats(st_r)};{rej_r};{flags:X}"
                      f"{self._diag_tail()}\r\n")
        elif self.mega:
            # Mega ohne Thermoelement: cycle;timer;f_Zuend;f_Rolle;Temp_Sonde;AFR (ganze Hz, AFR 20.00 ohne Sonde)
            self._out(f"{self.cycle};{rate:.2f};{int(f_ign)};{int(f_roll)};0;20.00\n")
            return dt
        else:
            self._out(f"{self.cycle};{rate:.2f};{f_ign:.2f};{f_roll:.2f};{egt1:.0f};{afr:.2f};{egt2:.0f}\n")
        if self.mega or self.mode == "D":                    # Autostopp nur im Modus N (F446 1.1.0)
            return dt
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


# ================================================================================================ PyST-Basis
# Stoerungen der Funkstrecke fuer 'SIM:basis:stoerung': Paketverlust [%], Zange weg [je Minute]
BASIS_FAULTS = {"sim_funkverlust": 8.0, "sim_zange_weg": 1.0}
BASIS_SIM_KEYS = {"sim_funkverlust": "0", "sim_zange_weg": "0", "sim_zange_mark": "1"}
PLUG_LOAD_W = {"SHELLY2": 180.0, "SHELLY1": 650.0, "TASMOTA": 2000.0, "ESPHOME": 40.0}


class BasisSimSerial(SimSerial):
    """PyST-Basis (G473 + ESP) mit gekoppelter Funk-Zange, WLAN und Steckdosen – nach
    docs/04_PST_Diag_1.md §10: '#CAP' mit ign=zange|none und hw=, D-Telegramm mit ign_peak_mv;ign_src,
    '#ZANGE', '#PAIR', '#NET', '#DOSE' und Ereigniszeilen 'E;…'. Simuliert Paketverlust der Funkstrecke
    (Zuendung 'stale'), Abbrueche der Zange, Akku, Signal, Marker-Tasten und Steckdosen mit Totmann."""

    def __init__(self, paired: bool = True, faults: Optional[Dict[str, float]] = None, seed: int = 11, **kw):
        from .basis import ZANGE_KEYS
        self.brng = random.Random(seed)
        self.z_paired = paired
        self.z_conn = paired
        self.z_back_at: Optional[float] = None          # Zange kommt wieder (nach Abbruch/Aus)
        self.z_akku = 87.0
        self.z_rssi = -58.0
        self.z_sparks = 0.0
        self.z_log_head = 52000
        self.z_rec = False
        self.z_settings = {k: d for k, (_t, d) in ZANGE_KEYS.items()}
        self.z_peak = 0.0
        self.z_stale = False
        self.z_last_ign = 0.0
        self.pair_until = 0.0
        self.pair_done_at: Optional[float] = None
        self.net = {"mode": "AP", "kanal": 6, "ssid": "PyST-Basis", "ip": "192.168.4.1", "clients": 1, "rssi": "",
                    "tcp": 0, "err": 0}
        self.plugs: Dict[int, dict] = {
            1: {"typ": "SHELLY2", "host": "192.168.4.20", "on": False, "until": None, "sek": 0},
            2: {"typ": "SHELLY1", "host": "192.168.4.21", "on": False, "until": None, "sek": 0},
        }
        self._wall = time.time()
        super().__init__(engine=SimEngine(rate=60.0), mega=False, diag=True, faults=None, seed=seed, **kw)
        self.tick_ns = 6.25
        self.tick_offset = (1 << 32) - int(2.0e9 / self.tick_ns)
        self.defaults.update(BASIS_SIM_KEYS)
        self.defaults["ign_delay_ms"] = "60"
        self.settings.update({k: v for k, v in self.defaults.items() if k not in self.settings})
        for k, v in (faults or {}).items():
            self.settings[k] = f"{v:g}"

    def burst_max(self) -> int:
        return 4096

    # --- Hilfen
    def _ms(self) -> int:
        return (int(self.t_sim * 1000) + self.ms_offset) & 0xFFFFFFFF

    def _event(self, *parts):
        self._out("E;" + ";".join([str(self._ms())] + [str(p) for p in parts]) + "\r\n")

    def _setf(self, key: str) -> float:
        try:
            return float(self.settings.get(key, 0) or 0)
        except ValueError:
            return 0.0

    def _zange_lost(self, back_after: Optional[float]):
        if self.z_conn:
            self.z_conn = False
            self._event("ZANGE", "verloren")
        self.z_back_at = time.time() + back_after if back_after else None

    def _zange_back(self):
        self.z_back_at = None
        if self.z_paired and not self.z_conn:
            self.z_conn = True
            self.z_rssi = -60.0
            self._event("ZANGE", "verbunden")

    # --- Hintergrund: Uhren, Funk, Steckdosen
    def _background(self):
        now = time.time()
        dt = max(0.0, min(1.0, now - self._wall))
        self._wall = now
        if not self.streaming:
            self.t_sim += dt                                   # Geraeteuhr laeuft weiter
        r = self.brng
        if self.z_conn:
            self.z_akku = max(3.0, self.z_akku - dt * 0.004)
            self.z_rssi = max(-90.0, min(-40.0, self.z_rssi + r.gauss(0.0, 0.6) * math.sqrt(dt * 10)
                                         + (-58.0 - self.z_rssi) * 0.2 * dt))
            if r.random() < self._setf("sim_zange_weg") / 60.0 * dt:
                self._zange_lost(r.uniform(2.0, 6.0))
            if r.random() < self._setf("sim_zange_mark") / 60.0 * dt:
                self._event("MARK", "zange", r.choice(["T1", "T2"]))
        if self.z_back_at is not None and now >= self.z_back_at:
            self._zange_back()
        if self.pair_done_at is not None and now >= self.pair_done_at:
            self.pair_done_at = None
            self.pair_until = 0.0
            self.z_paired = True
            self._zange_back()
        if self.pair_until and now > self.pair_until:
            self.pair_until = 0.0
        for p in self.plugs.values():
            if p["on"] and p["sek"] and p["until"] - now < p["sek"] / 3.0:
                p["until"] = now + p["sek"]                    # Totmann: die Basis erneuert, solange sie lebt

    def _frame_hook(self, f_ign: float, f_roll: float, dt: float):
        self.z_stale = False
        if not (self.z_paired and self.z_conn):
            self.z_peak = 0.0
            return 0.0, f_roll
        if f_ign > 0 and self.brng.random() < self._setf("sim_funkverlust") / 100.0:
            self.z_stale = True                                 # Paket fehlt: letzte bekannte Frequenz
            f_ign = self.z_last_ign
        self.z_last_ign = f_ign
        self.z_sparks += f_ign * dt
        rpm = f_ign * 60.0
        self.z_peak = max(0.0, 4300.0 - 0.07 * rpm + self.brng.gauss(0.0, 70.0)) if f_ign > 0 else 0.0
        return f_ign, f_roll

    def _extra_flags(self) -> int:
        fl = 0
        if not (self.z_paired and self.z_conn):
            fl |= 1                                             # Zuendung Timeout
        if self.z_stale:
            fl |= 1 << 4
        return fl

    def _diag_tail(self) -> str:
        src = 1 if self.z_paired and self.z_conn and self.z_peak > 0 else 0
        return f";{self.z_peak:.0f};{src}"

    # --- Befehle
    def _command(self, c: str):
        if c in "v?":
            self._out("PyST-Basis SIMULATOR 0.1.0;\r\nMessfrequenz [Hz]:60;\r\n")
            return
        super()._command(c)

    def _zange_line(self) -> str:
        mv = 3300 + int(self.z_akku * 9)
        z = self.z_settings
        return (f"#ZANGE;id=1;mac=34:85:18:a1:b2:c3;akku_mv={mv};akku_pct={self.z_akku:.0f};vbus=0;"
                f"rssi={self.z_rssi:.0f};funken={int(self.z_sparks)};log={self.z_log_head + int(self.z_sparks)}/"
                f"{self.z_log_head + 400000};blitz={z['blitz_mode']};phi={int(z['blitz_phi_x10']) / 10:.1f};"
                f"fw=0.1.0 SIM;sync_us={120 + self.brng.randint(0, 150) if self.z_conn else -1};"
                f"verbunden={1 if self.z_conn else 0}\r\n")

    def _diag_command(self, line: str):
        from .basis import PLUG_TYPES, ZANGE_KEYS
        parts = line[1:].strip().split()
        cmd = parts[0].upper() if parts else ""
        a = parts[1:]
        now = time.time()
        if cmd == "?":
            ch = "I,R" if self.z_paired else "R"
            ign = "zange" if self.z_paired else "none"
            self._out(f"#CAP;proto=1;fw=PyST-Basis 0.1.0 SIM;tick_ns=6.25;ch={ch};burst_max={self.burst_max()};"
                      f"modes=N,D;set=1;hw=rev31;ign={ign}\r\n")
        elif cmd == "ZANGE":
            sub = a[0].upper() if a else ""
            if not self.z_paired:
                self._out("#ERR;ZANGE;keine zange\r\n")
            elif not sub:
                self._out(self._zange_line())
                self._out("#OK;ZANGE;n=1\r\n")
            elif not self.z_conn:
                self._out("#ERR;ZANGE;nicht erreichbar\r\n")
            elif sub in ("SET", "GET") and len(a) >= 2:
                k = a[1]
                if k not in ZANGE_KEYS:
                    self._out(f"#ERR;ZANGE;unbekannt {k}\r\n")
                elif sub == "GET":
                    self._out(f"#OK;ZANGE;GET;{k}={self.z_settings[k]}\r\n")
                else:
                    try:
                        v = int(float(a[2]))
                    except (IndexError, ValueError):
                        self._out("#ERR;ZANGE;wert\r\n")
                        return
                    if (k == "blitz_pulse_us" and not 1 <= v <= 25) or (k == "blitz_mode" and v not in (0, 1, 2)):
                        self._out("#ERR;ZANGE;bereich\r\n")
                        return
                    self.z_settings[k] = str(v)
                    self._out(f"#OK;ZANGE;SET;{k}={v}\r\n")
            elif sub == "MARK":
                self._out("#OK;ZANGE;MARK\r\n")
                self._event("MARK", "pyst", "Marker")
            elif sub == "REC" and a[1:2] in (["0"], ["1"]):
                self.z_rec = a[1] == "1"
                self._out(f"#OK;ZANGE;REC;{a[1]}\r\n")
            elif sub in ("LOG", "IDENT"):
                self._out(f"#OK;ZANGE;{sub}\r\n")
            elif sub == "OFF":
                self._out("#OK;ZANGE;OFF\r\n")
                self._zange_lost(15.0)
            else:
                self._out("#ERR;ZANGE;parameter\r\n")
        elif cmd == "PAIR":
            sub = a[0].upper() if a else ""
            if sub == "1":
                self.pair_until = now + 30.0
                if not self.z_paired:
                    self.pair_done_at = now + 3.0             # Zange: T1+T2 gedrueckt
                self._out("#OK;PAIR;1\r\n")
            elif sub == "0":
                self.pair_until = 0.0
                self.pair_done_at = None
                self._out("#OK;PAIR;0\r\n")
            elif sub == "LIST":
                if self.z_paired:
                    self._out(f"#PAIR;id=1;mac=34:85:18:a1:b2:c3;typ=zange;sn=Z-0001;fw=0.1.0;"
                              f"verbunden={1 if self.z_conn else 0}\r\n")
                self._out("#OK;PAIR;LIST\r\n")
            elif sub == "DEL" and a[1:2] == ["1"] and self.z_paired:
                self._zange_lost(None)
                self.z_paired = False
                self._out("#OK;PAIR;DEL;1\r\n")
            else:
                self._out("#ERR;PAIR;parameter\r\n")
        elif cmd == "NET":
            sub = a[0].upper() if a else ""
            n = self.net
            if not sub:
                self._out(f"#NET;mode={n['mode']};kanal={n['kanal']};ssid={n['ssid']};ip={n['ip']};"
                          f"clients={n['clients']};rssi={n['rssi']};tcp={n['tcp']};err={n['err']}\r\n")
            elif sub == "AP" and len(a) >= 3 and a[1].isdigit() and 1 <= int(a[1]) <= 13:
                n.update(mode="AP", kanal=int(a[1]), ssid=a[2], ip="192.168.4.1", clients=0, rssi="", err=0)
                self._out("#OK;NET;AP\r\n")
            elif sub == "STA" and len(a) >= 2:
                n.update(mode="STA", ssid=a[1], ip="192.168.178.57", clients=0, rssi=-63, kanal=11, err=0)
                self._out("#OK;NET;STA\r\n")
            elif sub == "OFF":
                n.update(mode="OFF", ip="", clients=0, rssi="")
                self._out("#OK;NET;OFF\r\n")
            else:
                self._out("#ERR;NET;parameter\r\n")
        elif cmd == "DOSE":
            self._dose_command(a, now, PLUG_TYPES)
        else:
            super()._diag_command(line)

    def _dose_command(self, a: List[str], now: float, plug_types):
        sub = a[0].upper() if a else ""
        if not sub:
            for nr in sorted(self.plugs):
                p = self.plugs[nr]
                on = p["on"]
                mw = int(PLUG_LOAD_W.get(p["typ"], 100.0) * 1000 * (1 + self.brng.gauss(0, 0.01))) if on else 0
                rest = max(0, int(p["until"] - now)) if on and p["sek"] else 0
                self._out(f"#DOSE;nr={nr};typ={p['typ']};host={p['host']};an={1 if on else 0};p_mw={mw};"
                          f"erreichbar=1;rest_s={rest};fehler=0\r\n")
            self._out("#OK;DOSE\r\n")
        elif sub == "ADD" and len(a) >= 4 and a[1].isdigit():
            nr, typ = int(a[1]), a[2].upper()
            if not 1 <= nr <= 8 or typ not in plug_types:
                self._out("#ERR;DOSE;parameter\r\n")
                return
            self.plugs[nr] = {"typ": typ, "host": a[3], "on": False, "until": None, "sek": 0}
            self._out(f"#OK;DOSE;ADD;{nr}\r\n")
        elif sub == "DEL" and len(a) >= 2 and a[1].isdigit():
            if self.plugs.pop(int(a[1]), None) is None:
                self._out("#ERR;DOSE;unbekannt\r\n")
            else:
                self._out(f"#OK;DOSE;DEL;{a[1]}\r\n")
        elif sub == "SCAN":
            for k, (typ, host) in enumerate((("SHELLY2", "shellyplugsg3-a1b2c3.local"),
                                             ("TASMOTA", "tasmota-4f2a1c.local"),
                                             ("ESPHOME", "werkstatt-licht.local"))):
                self._out(f"#DOSE;SCAN;nr=0;typ={typ};host={host};ip=192.168.4.{30 + k}\r\n")
            self._out("#OK;DOSE;SCAN\r\n")
        elif sub.isdigit() and len(a) >= 2:
            nr = int(sub)
            p = self.plugs.get(nr)
            v = a[1].upper()
            if p is None or v not in ("0", "1", "TOGGLE"):
                self._out("#ERR;DOSE;parameter\r\n")
                return
            on = (not p["on"]) if v == "TOGGLE" else v == "1"
            sek = int(a[2]) if len(a) > 2 and a[2].isdigit() else 0
            changed = on != p["on"]
            p.update(on=on, sek=sek if on else 0, until=now + sek if on and sek else None)
            self._out(f"#OK;DOSE;{nr};{1 if on else 0}\r\n")
            if changed:
                self._event("DOSE", nr, 1 if on else 0)
        else:
            self._out("#ERR;DOSE;parameter\r\n")


# ================================================================================================ Zange per USB
ZANGE_GET_KEYS = ("edge_ign", "timeout_ign_ms", "imp_per_rev_x10", "deadtime_us", "plaus_lo_pct", "plaus_hi_pct",
                  "agc_mode", "probe_enable", "probe_edge", "blitz_mode", "blitz_phi_x10", "blitz_pulse_us",
                  "status_rate_hz", "tx_power")


def make_zange_log(seed: int = 5, start_idx: int = 1000, t0_us: int = 5_000_000, duration: float = 40.0) -> List[str]:
    """Log einer simulierten Aufnahme: Funken (mit Spitzenwert), Marker-Tasten, Sync-Paare, Status.
    -> Textzeilen 'L;idx;art;lokal_us;…' (Format siehe basis.LogEntry)."""
    rng = random.Random(seed)
    eng = SimEngine(rate=200.0, seed=seed)
    eng.reset()
    lines = []
    idx = start_idx
    t = 0.0
    phase = 0.0
    dt = 1.0 / 200.0
    next_sync, next_status = 0.0, 0.0
    marks = {12.0: 1, 25.0: 2}
    akku = 91.0
    while t < duration:
        _d, _r, f_ign, _f_roll, *_x = eng.next_frame()
        rpm = f_ign * 60.0
        if f_ign > 0:
            p0 = phase
            phase += f_ign * dt
            k = math.floor(p0) + 1
            while k <= phase:
                ts = t + (k - p0) / f_ign
                peak = max(0, int(4300 - 0.07 * rpm + rng.gauss(0, 70)))
                lines.append(f"L;{idx};F;{t0_us + int(ts * 1e6)};0;0;{peak}")
                idx += 1
                if rng.random() < 0.004:                          # Nachschwinger: verworfen gemeldet
                    lines.append(f"L;{idx};F;{t0_us + int((ts + 0.2 / f_ign) * 1e6)};0;1;{peak // 3}")
                    idx += 1
                k += 1
        for tm, key in list(marks.items()):
            if t >= tm:
                lines.append(f"L;{idx};M;{t0_us + int(t * 1e6)};{key}")
                idx += 1
                del marks[tm]
        if t >= next_sync:
            lines.append(f"L;{idx};Y;{t0_us + int(t * 1e6)};{123456789 + int(t * 1e6) + rng.randint(-40, 40)}")
            idx += 1
            next_sync += 1.0
        if t >= next_status:
            akku -= 0.05
            lines.append(f"L;{idx};S;{t0_us + int(t * 1e6)};{3300 + int(akku * 9)};{akku:.0f};16")
            idx += 1
            next_status += 5.0
        t += dt
    return lines


class ZangeUsbSimSerial(SimSerial):
    """Zange direkt per USB (USB-Serial-JTAG): Mega-kompatibles ASCII + PST-Diag 1 mit ch=I, tick_ns=1000,
    Rolle = 0. Dazu '#LOG INFO' / '#LOG GET von n' (Logzeilen 'L;…')."""

    def __init__(self, seed: int = 3, **kw):
        from .basis import ZANGE_KEYS
        self.log = make_zange_log()
        self.log_tail = int(self.log[0].split(";")[1])
        self.log_head = int(self.log[-1].split(";")[1]) + 1
        self.zrng = random.Random(seed)
        self.peak = 0.0
        super().__init__(engine=SimEngine(rate=50.0, n_hold=1800.0), mega=True, diag=True, seed=seed, **kw)
        self.tick_ns = 1000.0
        self.tick_offset = (1 << 32) - int(2.0e9 / self.tick_ns)
        self.defaults.update({k: d for k, (_t, d) in ZANGE_KEYS.items()})
        self.settings = dict(self.defaults)

    def burst_max(self) -> int:
        return 2000

    def _frame_hook(self, f_ign: float, f_roll: float, dt: float):
        self.peak = max(0.0, 4300.0 - 4.2 * f_ign + self.zrng.gauss(0, 70)) if f_ign > 0 else 0.0
        return f_ign, 0.0

    def _diag_tail(self) -> str:
        return f";{self.peak:.0f};{1 if self.peak > 0 else 0}"

    def _command(self, c: str):
        if c == "v":
            self._out("Version PyST-Zange 0.1.0 SIM;\r\n")
        elif c == "e":
            pass                                                  # kein Klimasensor
        else:
            super()._command(c)

    def _diag_command(self, line: str):
        parts = line[1:].strip().split()
        cmd = parts[0].upper() if parts else ""
        a = parts[1:]
        if cmd == "?":
            self._out(f"#CAP;proto=1;fw=PyST-Zange 0.1.0 SIM;tick_ns=1000;ch=I;burst_max={self.burst_max()};"
                      f"modes=N,D;set=1;hw=zange-c3\r\n")
        elif cmd == "GET":
            for k in ZANGE_GET_KEYS:
                self._out(f"#SET;{k}={self.settings[k]}\r\n")
            self._out("#OK;GET\r\n")
        elif cmd == "LOG":
            sub = a[0].upper() if a else ""
            if sub == "INFO":
                self._out(f"#LOG;head={self.log_head};tail={self.log_tail};n={self.log_head - self.log_tail}\r\n")
                self._out("#OK;LOG;INFO\r\n")
            elif sub == "GET" and len(a) >= 3:
                try:
                    start, n = int(a[1]), int(a[2])
                except ValueError:
                    self._out("#ERR;LOG;parameter\r\n")
                    return
                a0 = max(start, self.log_tail) - self.log_tail
                part = self.log[a0:a0 + max(0, n)]
                self._out("".join(x + "\r\n" for x in part))
                self._out(f"#OK;LOG;GET;n={len(part)}\r\n")
            elif sub == "CLEAR":
                self.log_tail = self.log_head
                self.log = []
                self._out("#OK;LOG;CLEAR\r\n")
            else:
                self._out("#ERR;LOG;parameter\r\n")
        else:
            super()._diag_command(line)
