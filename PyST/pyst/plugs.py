"""
WLAN-Steckdosen: Gebläse, Absaugung, Heizlüfter … schalten – über die PyST-Basis ('#DOSE …') oder direkt
aus PyST per HTTP, wenn PC und Steckdosen im selben Netz sind (ohne Basis). Ohne Qt.

Typen (wie in der Basis):
  SHELLY2  Shelly Gen2/Gen3/Gen4 (RPC):  /rpc/Switch.Set?id=<k>&on=true&toggle_after=<s>, /rpc/Switch.GetStatus?id=<k>
  SHELLY1  Shelly Gen1:                  /relay/<k>?turn=on&timer=<s>, /status
  TASMOTA  Tasmota:                      /cm?cmnd=Power<k+1> On, PulseTime<k+1> (Rueckfallzeit), Status 10 (Leistung)
  ESPHOME  ESPHome-Web-Server (REST):    POST /switch/<id>/turn_on|turn_off, GET /switch/<id>, GET /sensor/<id>

Totmann: Einschalten mit Rueckfallzeit (Shelly toggle_after/timer, Tasmota PulseTime) und zyklisch erneuern,
solange PyST laeuft. Je Steckdose waehlbar: „fällt aus“ (z.B. Heizlüfter: mit Rueckfallzeit) oder „bleibt an“
(Absaugung, Lüfter: ohne). toggle_after/timer schalten UM -> nur zusammen mit "ein" senden. Ob ein erneutes
Switch.Set den laufenden Timer neu startet, ist bei Shelly nicht dokumentiert: PyST prueft timer_started_at.
Tasmota: PulseTime ist eine gespeicherte Einstellung (gilt fuer jedes folgende "Power On", das den Timer neu
startet). ESPHome kennt keine Rueckfallzeit ueber die REST-Schnittstelle (nur zyklisch erneuern, Hinweis im UI).
Beim Hinzufuegen setzt PyST den Einschaltzustand nach Stromausfall auf „aus“ (Shelly initial_state/default_state,
Tasmota PowerOnState 0). Shelly mit Passwort (Digest-Auth) wird erkannt, aber nicht unterstuetzt; Geraete ohne
Relais (z.B. Shelly Plug PM Gen3) werden abgewiesen.
Empfohlen: Shelly Plug S/M Gen3 (RPC), als Tasmota-Geraet z.B. NOUS A1T.
Komfortfunktion, KEINE Sicherheitsfunktion, kein Not-Aus.
"""
import json
import queue
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Dict, List, Optional, Tuple

from .basis import MAX_PLUGS, PLUG_TYPES, PlugState

HTTP_TIMEOUT = 2.0
NO_FALLBACK = {"ESPHOME"}       # ohne Rueckfall-Timer (nur zyklisch erneuern)
RENEW_FRACTION = 3.0            # Totmann alle totmann_s/3 erneuern


@dataclass
class PlugConfig:
    nr: int
    typ: str = "SHELLY2"
    host: str = ""
    name: str = ""
    channel: int = 0              # Relais-/Switch-Kanal
    esphome_id: str = "relay"     # ESPHome: Switch-ID
    power_id: str = "power"       # ESPHome: Sensor-ID der Leistung
    fail_on: bool = False         # True = „bleibt an“ (ohne Rueckfallzeit), False = „fällt aus“
    totmann_s: int = 60           # Rueckfallzeit [s]
    auto_lauf: bool = False       # beim Laufstart ein
    auto_dauer: bool = False      # bei Dauermessung ein
    nachlauf_s: float = 30.0      # nach Ende so lange weiterlaufen

    def label(self) -> str:
        return self.name or f"Steckdose {self.nr}"

    @staticmethod
    def from_dict(d: dict) -> "PlugConfig":
        return PlugConfig(**{k: v for k, v in d.items() if k in PlugConfig.__dataclass_fields__})


# ---------------------------------------------------------------------------------------------- HTTP
def _url(host: str, path: str) -> str:
    host = host.strip()
    if not host.startswith("http"):
        host = "http://" + host
    return host.rstrip("/") + path


def http_get(host: str, path: str, timeout: float = HTTP_TIMEOUT, method: str = "GET"):
    req = urllib.request.Request(_url(host, path), method=method, data=b"" if method == "POST" else None)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read().decode("utf-8", "replace")
    try:
        return json.loads(body) if body.strip() else {}
    except ValueError:
        return {"_text": body}


def _q(cmd: str) -> str:
    return "/cm?cmnd=" + urllib.parse.quote(cmd)


class PlugDriver:
    """Schalten und Abfragen einer Steckdose direkt per HTTP."""

    def __init__(self, cfg: PlugConfig, timeout: float = HTTP_TIMEOUT):
        self.cfg = cfg
        self.timeout = timeout

    def _get(self, path: str, method: str = "GET"):
        return http_get(self.cfg.host, path, self.timeout, method)

    def init_defaults(self) -> str:
        """Einschaltzustand nach Stromausfall = aus. Liefert einen Hinweis ('' = ok)."""
        c, k = self.cfg, self.cfg.channel
        try:
            if c.typ == "SHELLY2":
                cfg = urllib.parse.quote(json.dumps({"initial_state": "off"}, separators=(",", ":")))
                self._get(f"/rpc/Switch.SetConfig?id={k}&config={cfg}")
            elif c.typ == "SHELLY1":
                self._get(f"/settings/relay/{k}?default_state=off")
            elif c.typ == "TASMOTA":
                self._get(_q("PowerOnState 0"))
            else:
                return "ESPHome: Einschaltzustand bitte in der ESPHome-Konfiguration (restore_mode) festlegen"
        except (urllib.error.URLError, OSError, ValueError) as exc:
            return f"Einschaltzustand nicht gesetzt: {getattr(exc, 'reason', exc)}"
        return ""

    def set(self, on: bool, fallback_s: Optional[float] = None) -> PlugState:
        c, k = self.cfg, self.cfg.channel
        fb = int(fallback_s) if fallback_s and on else 0
        if c.typ == "SHELLY2":
            q = f"/rpc/Switch.Set?id={k}&on={'true' if on else 'false'}" + (f"&toggle_after={fb}" if fb else "")
            self._get(q)
        elif c.typ == "SHELLY1":
            self._get(f"/relay/{k}?turn={'on' if on else 'off'}" + (f"&timer={fb}" if fb else ""))
        elif c.typ == "TASMOTA":
            if on:
                # PulseTime: 1…111 = 0,1-s-Schritte, ab 112 = Sekunden + 100; 0 = aus (gilt fuer alle folgenden "On")
                pt = 0 if not fb else (fb * 10 if fb <= 11 else fb + 100)
                self._get(_q(f"PulseTime{k + 1} {min(pt, 64900)}"))
            self._get(_q(f"Power{k + 1} {'On' if on else 'Off'}"))
        elif c.typ == "ESPHOME":
            self._get(f"/switch/{c.esphome_id}/{'turn_on' if on else 'turn_off'}", method="POST")
        else:
            raise ValueError(f"unbekannter Typ {c.typ}")
        st = self.status()
        if st.on is None:
            st.on = on
        return st

    def status(self) -> PlugState:
        c, k = self.cfg, self.cfg.channel
        st = PlugState(nr=c.nr, typ=c.typ, host=c.host, name=c.name, fail_on=c.fail_on)
        try:
            if c.typ == "SHELLY2":
                d = self._get(f"/rpc/Switch.GetStatus?id={k}")
                st.on = bool(d.get("output")) if "output" in d else None
                if "apower" in d:
                    st.power_w = float(d["apower"])
                if d.get("timer_started_at") is not None:
                    st.extra["timer_started_at"] = d["timer_started_at"]
                if d.get("timer_duration") and d.get("timer_started_at"):
                    st.rest_s = max(0.0, float(d["timer_started_at"]) + float(d["timer_duration"]) - time.time())
            elif c.typ == "SHELLY1":
                d = self._get("/status")
                rel = (d.get("relays") or [{}])
                r = rel[k] if k < len(rel) else {}
                st.on = bool(r.get("ison")) if "ison" in r else None
                if r.get("has_timer"):
                    st.rest_s = float(r.get("timer_remaining", 0))
                meters = d.get("meters") or d.get("emeters") or []
                if k < len(meters) and "power" in meters[k]:
                    st.power_w = float(meters[k]["power"])
            elif c.typ == "TASMOTA":
                d = self._get(_q(f"Power{k + 1}"))
                v = d.get(f"POWER{k + 1}", d.get("POWER"))
                st.on = None if v is None else str(v).upper() == "ON"
                e = self._get(_q("Status 10")).get("StatusSNS", {}).get("ENERGY", {})
                pw = e.get("Power")
                if isinstance(pw, list):
                    pw = pw[k] if k < len(pw) else None
                if pw is not None:
                    st.power_w = float(pw)
            elif c.typ == "ESPHOME":
                d = self._get(f"/switch/{c.esphome_id}")
                if "value" in d:
                    st.on = bool(d["value"])
                elif "state" in d:
                    st.on = str(d["state"]).upper() == "ON"
                if c.power_id:
                    try:
                        s = self._get(f"/sensor/{c.power_id}")
                        if isinstance(s.get("value"), (int, float)):
                            st.power_w = float(s["value"])
                    except (urllib.error.URLError, OSError, ValueError):
                        pass
            st.reachable = True
        except (urllib.error.URLError, OSError, ValueError) as exc:
            st.reachable = False
            st.error = str(getattr(exc, "reason", exc))
        return st


class PlugRejected(ValueError):
    """Geraet ist keine schaltbare Steckdose (kein Relais) oder braucht ein Passwort."""


def check_shelly(host: str, timeout: float = HTTP_TIMEOUT) -> str:
    """GET /shelly: -> 'SHELLY2'/'SHELLY1', '' wenn kein Shelly. PlugRejected bei Passwort (auth_en) oder
    Geraet ohne Relais (z.B. Plug PM Gen3: kein 'switch:0')."""
    try:
        d = http_get(host, "/shelly", timeout)
    except (urllib.error.URLError, OSError, ValueError):
        return ""
    if not isinstance(d, dict) or not ("gen" in d or "type" in d or "mac" in d):
        return ""
    gen = int(d.get("gen", 1) or 1)
    if d.get("auth_en") or d.get("auth"):
        raise PlugRejected("Shelly mit Passwort (Digest-Auth) – bitte Passwort in der Shelly-App abschalten")
    if gen >= 2:
        try:
            st = http_get(host, "/rpc/Shelly.GetStatus", timeout)
            if isinstance(st, dict) and st and not any(k.startswith("switch:") for k in st):
                raise PlugRejected(f"{d.get('model', 'Shelly')}: kein Relais (nur Messung) – nicht schaltbar")
        except (urllib.error.URLError, OSError):
            pass
        return "SHELLY2"
    return "SHELLY1"


def detect_type(host: str, timeout: float = HTTP_TIMEOUT) -> str:
    """Typ einer Steckdose am Host erraten: SHELLY2, SHELLY1, TASMOTA, ESPHOME oder ''.
    PlugRejected bei Shelly mit Passwort oder ohne Relais."""
    t = check_shelly(host, timeout)
    if t:
        return t
    try:
        d = http_get(host, _q("Status"), timeout)
        if isinstance(d, dict) and "Status" in d:
            return "TASMOTA"
    except (urllib.error.URLError, OSError, ValueError):
        pass
    try:
        d = http_get(host, "/", timeout)
        if "esphome" in json.dumps(d).lower():
            return "ESPHOME"
    except (urllib.error.URLError, OSError, ValueError):
        pass
    return ""


def scan(timeout: float = 2.5) -> List[Dict[str, str]]:
    """Steckdosen im Netz per mDNS suchen -> [{typ, host, ip, name}] (Typ ggf. noch leer)."""
    from . import mdns
    out, seen = [], set()
    for service, typ in (("_shelly._tcp.local", "SHELLY2"), ("_esphomelib._tcp.local", "ESPHOME"),
                         ("_http._tcp.local", "")):
        for d in mdns.browse(service, timeout / 3):
            host = str(d.get("ip") or d.get("host") or "").rstrip(".")
            if not host or host in seen:
                continue
            seen.add(host)
            t = typ
            txt = d.get("txt") or {}
            if typ == "SHELLY2" and str(txt.get("gen", "2")) == "1":
                t = "SHELLY1"
            name = str(d.get("name", ""))
            if not t and "tasmota" in name.lower():
                t = "TASMOTA"
            if not t and "shelly" in name.lower():
                t = "SHELLY1"
            out.append({"typ": t, "host": host, "ip": str(d.get("ip", "")), "name": name})
    return out


# ---------------------------------------------------------------------------------------------- Automatik
class PlugAutomation:
    """Beim Laufstart (bzw. bei Dauermessung) ein, nach dem Ende + Nachlaufzeit aus.
    update() regelmaessig mit dem aktuellen Zustand aufrufen; liefert Schaltbefehle [(nr, an)]."""

    def __init__(self):
        self.on_by_auto: Dict[int, bool] = {}
        self.off_due: Dict[int, float] = {}

    def update(self, cfgs: List[PlugConfig], lauf: bool, dauer: bool, now: Optional[float] = None) -> List[Tuple[int, bool]]:
        now = time.time() if now is None else now
        out = []
        for c in cfgs:
            want = (c.auto_lauf and lauf) or (c.auto_dauer and dauer)
            if want:
                self.off_due.pop(c.nr, None)
                if not self.on_by_auto.get(c.nr):
                    self.on_by_auto[c.nr] = True
                    out.append((c.nr, True))
            elif self.on_by_auto.get(c.nr):
                due = self.off_due.setdefault(c.nr, now + max(0.0, float(c.nachlauf_s)))
                if now >= due:
                    self.off_due.pop(c.nr, None)
                    self.on_by_auto[c.nr] = False
                    out.append((c.nr, False))
        return out

    def pending_text(self, nr: int, now: Optional[float] = None) -> str:
        now = time.time() if now is None else now
        if nr in self.off_due:
            return f"aus in {max(0, self.off_due[nr] - now):.0f} s"
        return "Automatik an" if self.on_by_auto.get(nr) else ""


# ---------------------------------------------------------------------------------------------- Verwaltung
class PlugManager:
    """Steckdosen ueber die Basis ('basis') oder direkt per HTTP ('direkt'). Netzwerkzugriffe laufen in einem
    eigenen Thread; Ergebnisse holt poll() ab (aus dem GUI-Takt). Zustand in self.states, Meldungen in
    self.messages."""

    def __init__(self, configs: Optional[List[PlugConfig]] = None, mode: str = "direkt",
                 get_link: Callable[[], object] = lambda: None):
        self.configs: Dict[int, PlugConfig] = {c.nr: c for c in (configs or [])}
        self.mode = mode
        self.get_link = get_link
        self.states: Dict[int, PlugState] = {}
        self.wanted: Dict[int, bool] = {}          # zuletzt befohlener Zustand (fuer das Erneuern)
        self.renewed: Dict[int, float] = {}
        self.messages: List[str] = []
        self.found: List[Dict[str, str]] = []
        self.automation = PlugAutomation()
        self._jobs: "queue.Queue" = queue.Queue()
        self._results: "queue.Queue" = queue.Queue()
        self._busy = 0
        self._stop = False
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    # --- Hilfen
    def close(self):
        self._stop = True
        self._jobs.put(None)

    def _worker(self):
        while not self._stop:
            job = self._jobs.get()
            if job is None:
                break
            fn, args = job
            try:
                res = fn(*args)
            except Exception as exc:                          # Netzwerkfehler duerfen nichts stoppen
                res = ("msg", f"Fehler: {exc}")
            self._results.put(res)

    def _submit(self, fn, *args):
        self._busy += 1
        self._jobs.put((fn, args))

    def idle(self) -> bool:
        return self._busy == 0

    def wait_idle(self, timeout: float = 10.0) -> bool:
        """Fuer Tests/Kommandozeile: warten, bis alle Auftraege erledigt sind."""
        end = time.time() + timeout
        while time.time() < end:
            self.poll(renew=False)
            if self._busy == 0:
                return True
            time.sleep(0.01)
        return False

    def poll(self, renew: bool = True) -> bool:
        """Ergebnisse uebernehmen, Totmann erneuern. True = etwas hat sich geaendert."""
        changed = False
        while True:
            try:
                res = self._results.get_nowait()
            except queue.Empty:
                break
            self._busy = max(0, self._busy - 1)
            changed = True
            kind = res[0] if isinstance(res, tuple) else ""
            if kind == "state":
                if len(res) > 2:
                    self.messages.append(res[2])
                st = res[1]
                old = self.states.get(st.nr)
                if old is not None and st.reachable is False and old.on is not None:
                    st.on = old.on
                self.states[st.nr] = st
            elif kind == "states":
                for st in res[1]:
                    if st.nr not in self.configs:
                        if self.mode != "basis":
                            continue                          # inzwischen geloescht
                        self.configs[st.nr] = PlugConfig(st.nr, st.typ or "SHELLY2", st.host)
                    else:
                        c = self.configs[st.nr]
                        c.typ, c.host = st.typ or c.typ, st.host or c.host
                    st.name = st.name or self.configs[st.nr].name
                    self.states[st.nr] = st
                if self.mode == "basis":
                    known = {st.nr for st in res[1]}
                    for nr in list(self.states):
                        if nr not in known:
                            self.states.pop(nr)
            elif kind == "found":
                self.found = res[1]
                self.messages.append(f"Suche: {len(res[1])} Gerät(e) gefunden")
            elif kind == "msg":
                self.messages.append(res[1])
        if renew and self.mode == "direkt":
            self._renew()
        return changed

    def _renew(self):
        now = time.time()
        for nr, on in self.wanted.items():
            c = self.configs.get(nr)
            if not on or c is None or c.fail_on or c.totmann_s <= 0:
                continue
            if now - self.renewed.get(nr, 0.0) >= max(2.0, c.totmann_s / RENEW_FRACTION):
                self.renewed[nr] = now
                self._submit(self._do_set, c, True)

    # --- Auftraege
    def _do_set(self, c: PlugConfig, on):
        fb = None if c.fail_on else c.totmann_s
        if self.mode == "basis":
            link = self.get_link()
            if link is None:
                return "msg", "Basis nicht verbunden"
            lines = link.dose_switch(c.nr, on, fb if on is True else None)
            if not link.ok(lines):
                return "msg", f"{c.label()}: " + (lines[-1] if lines else "keine Antwort")
            return "states", link.dose_list()
        if on == "TOGGLE":
            cur = PlugDriver(c).status()
            on = not cur.on
            self.wanted[c.nr] = on
        before = self.states.get(c.nr)
        st = PlugDriver(c).set(bool(on), fb)
        st.name = c.name
        if (on is True and fb and c.typ == "SHELLY2" and before is not None and before.on
                and before.extra.get("timer_started_at") is not None
                and st.extra.get("timer_started_at") == before.extra.get("timer_started_at")):
            return "state", st, f"{c.label()}: Rückfall-Timer wurde nicht neu gestartet (Shelly-Firmware)"
        return "state", st

    def _do_refresh(self):
        if self.mode == "basis":
            link = self.get_link()
            if link is None:
                return "msg", "Basis nicht verbunden"
            return "states", link.dose_list()
        return "states", [PlugDriver(c).status() for c in list(self.configs.values())]

    def _do_add(self, c: PlugConfig):
        if self.mode == "basis":
            link = self.get_link()
            if link is None:
                return "msg", "Basis nicht verbunden"
            lines = link.dose_add(c.nr, c.typ, c.host)
            if not link.ok(lines):
                return "msg", f"Hinzufügen abgelehnt: " + (lines[-1] if lines else "keine Antwort")
            return "states", link.dose_list()
        try:
            if c.typ in ("", "SHELLY2", "SHELLY1"):
                found = check_shelly(c.host)
                c.typ = found or c.typ or detect_type(c.host) or "SHELLY2"
        except PlugRejected as exc:
            self.configs.pop(c.nr, None)
            return "msg", f"{c.host}: {exc}"
        drv = PlugDriver(c)
        note = drv.init_defaults()
        st = drv.status()
        if c.typ in NO_FALLBACK:
            note = (note + " · " if note else "") + "ESPHome: kein Rückfall-Timer, PyST erneuert nur zyklisch"
        return ("state", st, f"{c.label()}: {note}") if note else ("state", st)

    def _do_delete(self, nr: int):
        if self.mode == "basis":
            link = self.get_link()
            if link is not None:
                link.dose_delete(nr)
                return "states", link.dose_list()
        return "msg", f"Steckdose {nr} entfernt"

    def _do_scan(self):
        if self.mode == "basis":
            link = self.get_link()
            if link is None:
                return "msg", "Basis nicht verbunden"
            return "found", [{"typ": s.typ, "host": s.host, "ip": s.extra.get("ip", ""), "name": s.name}
                             for s in link.dose_scan()]
        found = scan()
        for f in found:
            try:
                f["typ"] = detect_type(f["host"], timeout=1.0) or f["typ"]
            except PlugRejected as exc:
                f["typ"] = ""
                f["hinweis"] = str(exc)
        return "found", found

    # --- oeffentlich
    def free_nr(self) -> int:
        for nr in range(1, MAX_PLUGS + 1):
            if nr not in self.configs:
                return nr
        return 0

    def add(self, c: PlugConfig):
        if c.typ and c.typ not in PLUG_TYPES:
            raise ValueError(f"Typ {c.typ} unbekannt ({', '.join(PLUG_TYPES)})")
        if not (1 <= c.nr <= MAX_PLUGS):
            raise ValueError(f"Nummer 1…{MAX_PLUGS}")
        self.configs[c.nr] = c
        self._submit(self._do_add, c)

    def delete(self, nr: int):
        self.configs.pop(nr, None)
        self.states.pop(nr, None)
        self.wanted.pop(nr, None)
        self._submit(self._do_delete, nr)

    def switch(self, nr: int, on):
        c = self.configs.get(nr)
        if c is None:
            return
        if on != "TOGGLE":
            self.wanted[nr] = bool(on)
            self.renewed[nr] = time.time()
        self._submit(self._do_set, c, on)

    def refresh(self):
        self._submit(self._do_refresh)

    def scan(self):
        self._submit(self._do_scan)

    def automatic(self, lauf: bool, dauer: bool, now: Optional[float] = None) -> List[Tuple[int, bool]]:
        acts = self.automation.update(list(self.configs.values()), lauf, dauer, now)
        for nr, on in acts:
            self.switch(nr, on)
            c = self.configs.get(nr)
            self.messages.append(f"Automatik: {c.label() if c else nr} {'ein' if on else 'aus'}")
        return acts

    def to_settings(self) -> List[dict]:
        return [asdict(c) for c in sorted(self.configs.values(), key=lambda c: c.nr)]


# ---------------------------------------------------------------------------------------------- Simulator
class _PlugSimHandler(BaseHTTPRequestHandler):
    server_version = "PlugSim/1"

    def log_message(self, *_a):
        pass

    def _send(self, obj, code=200):
        body = obj.encode() if isinstance(obj, str) else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html" if isinstance(obj, str) else "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self.do_GET()

    def do_GET(self):
        dev: "PlugSimDevice" = self.server.dev
        u = urllib.parse.urlparse(self.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        dev.requests.append(self.path)
        try:
            self._send(dev.handle(u.path, q, self.command))
        except KeyError:
            self._send({"code": 404, "message": "not found"}, 404)


class PlugSimDevice:
    """Eine simulierte Steckdose mit HTTP-Schnittstelle wie das echte Geraet (Antworten nach den
    Beispielen der Hersteller-Dokumentation). Fuer Tests und zum Vorfuehren ohne Hardware."""

    def __init__(self, typ: str = "SHELLY2", load_w: float = 1200.0, port: int = 0, auth: bool = False,
                 relay: bool = True):
        self.typ = typ
        self.auth = auth                   # Shelly: Passwort gesetzt (auth_en)
        self.relay = relay                 # False = nur Messgeraet (z.B. Plug PM Gen3)
        self.load_w = load_w
        self.on = False
        self.off_at: Optional[float] = None
        self.timer_s = 0.0
        self.timer_start = 0.0
        self.pulsetime = 0
        self.initial = ""                  # gesetzter Einschaltzustand nach Stromausfall
        self.requests: List[str] = []
        self.srv = ThreadingHTTPServer(("127.0.0.1", port), _PlugSimHandler)
        self.srv.dev = self
        self._t = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self._t.start()

    @property
    def host(self) -> str:
        return f"127.0.0.1:{self.srv.server_address[1]}"

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()

    def _update(self):
        if self.on and self.off_at is not None and time.time() >= self.off_at:
            self.on, self.off_at = False, None

    def _set(self, on: bool, timer: float = 0.0):
        self.on = on
        self.timer_s = timer if on and timer else 0.0
        self.timer_start = time.time()
        self.off_at = time.time() + timer if on and timer else None

    @property
    def power(self) -> float:
        return round(self.load_w * (1.0 + 0.01 * ((time.time() * 7) % 3 - 1)), 1) if self.on else 0.0

    def handle(self, path: str, q: Dict[str, str], method: str):
        self._update()
        t = self.typ
        if t == "SHELLY2":
            if path == "/shelly":
                return {"name": "Sim", "id": "shellyplugsg3-sim", "mac": "AABBCCDDEEFF", "model": "S3PL-00112EU",
                        "gen": 3, "fw_id": "20250101-000000/1.5.0", "ver": "1.5.0", "app": "PlugSG3",
                        "auth_en": self.auth, "auth_domain": None}
            if path == "/rpc/Shelly.GetStatus":
                d = {"sys": {"uptime": 100}, "wifi": {"rssi": -58}}
                if self.relay:
                    d["switch:0"] = {"id": 0, "output": self.on}
                else:
                    d["pm1:0"] = {"id": 0, "apower": 0.0}
                return d
            if path == "/rpc/Switch.SetConfig":
                self.initial = "off"
                return {"restart_required": False}
            if path == "/rpc/Switch.Set":
                was = self.on
                self._set(q.get("on") == "true", float(q.get("toggle_after", 0) or 0))
                return {"was_on": was}
            if path == "/rpc/Switch.GetStatus":
                d = {"id": int(q.get("id", 0)), "source": "HTTP_in", "output": self.on, "apower": self.power,
                     "voltage": 231.2, "current": round(self.power / 231.2, 3), "aenergy": {"total": 1234.5},
                     "temperature": {"tC": 31.2, "tF": 88.2}}
                if self.off_at:
                    d.update(timer_started_at=self.timer_start, timer_duration=self.timer_s)
                return d
        elif t == "SHELLY1":
            if path == "/shelly":
                return {"type": "SHPLG-S", "mac": "AABBCCDDEEFF", "auth": False, "fw": "20230913-114008/v1.14.0"}
            if path.startswith("/settings/relay/"):
                self.initial = q.get("default_state", "")
                return {"name": None, "ison": self.on, "default_state": self.initial}
            if path.startswith("/relay/"):
                if "turn" in q:
                    self._set(q["turn"] == "on", float(q.get("timer", 0) or 0))
                return {"ison": self.on, "has_timer": self.off_at is not None,
                        "timer_started": int(self.timer_start) if self.off_at else 0,
                        "timer_duration": self.timer_s, "timer_remaining": max(0, (self.off_at or 0) - time.time())
                        if self.off_at else 0, "overpower": False, "source": "http"}
            if path == "/status":
                return {"wifi_sta": {"connected": True, "ssid": "PyST-Basis", "rssi": -58},
                        "relays": [{"ison": self.on, "has_timer": self.off_at is not None,
                                    "timer_remaining": max(0, (self.off_at or 0) - time.time()) if self.off_at else 0,
                                    "overpower": False, "source": "http"}],
                        "meters": [{"power": self.power, "overpower": 0.0, "is_valid": True, "total": 4711}],
                        "temperature": 33.1}
        elif t == "TASMOTA":
            if path == "/cm":
                cmd = q.get("cmnd", "").strip()
                low = cmd.lower()
                if low.startswith("poweronstate"):
                    self.initial = cmd.split()[-1]
                    return {"PowerOnState": int(self.initial) if self.initial.isdigit() else 3}
                if low.startswith("pulsetime"):
                    parts = cmd.split()
                    if len(parts) > 1:
                        self.pulsetime = int(parts[1])
                    return {"PulseTime1": {"Set": self.pulsetime, "Remaining": 0}}
                if low.startswith("power"):
                    parts = cmd.split()
                    if len(parts) > 1:
                        on = parts[1].lower() in ("on", "1")
                        pt = self.pulsetime
                        secs = pt / 10.0 if 0 < pt <= 111 else (pt - 100 if pt > 111 else 0)
                        self._set(on, secs)
                    return {"POWER1": "ON" if self.on else "OFF"}
                if low == "status 10":
                    return {"StatusSNS": {"Time": "2026-10-26T12:00:00", "ENERGY": {
                        "TotalStartTime": "2026-01-01T00:00:00", "Total": 12.3, "Yesterday": 0.5, "Today": 0.2,
                        "Power": int(self.power), "ApparentPower": int(self.power), "ReactivePower": 0,
                        "Factor": 1.0, "Voltage": 230, "Current": round(self.power / 230, 3)}}}
                if low == "status":
                    return {"Status": {"Module": 0, "DeviceName": "Tasmota", "FriendlyName": ["Tasmota"],
                                       "Power": 1 if self.on else 0}}
        elif t == "ESPHOME":
            if path == "/":
                return "<!DOCTYPE html><html><head><title>sim</title></head><body>ESPHome Web Server</body></html>"
            if path.startswith("/switch/"):
                parts = path.split("/")
                if len(parts) >= 4 and method == "POST":
                    if parts[3] == "turn_on":
                        self._set(True)
                    elif parts[3] == "turn_off":
                        self._set(False)
                    elif parts[3] == "toggle":
                        self._set(not self.on)
                    return ""
                return {"id": f"switch-{parts[2]}", "value": self.on, "state": "ON" if self.on else "OFF"}
            if path.startswith("/sensor/"):
                return {"id": f"sensor-{path.split('/')[2]}", "value": self.power, "state": f"{self.power:.1f} W"}
        raise KeyError(path)


def start_demo_plugs() -> List[Tuple[PlugSimDevice, PlugConfig]]:
    """Vier simulierte Steckdosen (je ein Typ) auf 127.0.0.1 – „direkt aus PyST“ ohne Hardware vorfuehren."""
    out = []
    for nr, (typ, name, load, fail_on, auto) in enumerate((
            ("SHELLY2", "Gebläse", 180.0, True, True), ("SHELLY1", "Absaugung", 650.0, True, True),
            ("TASMOTA", "Heizlüfter", 2000.0, False, False), ("ESPHOME", "Licht", 40.0, True, False)), 1):
        dev = PlugSimDevice(typ, load)
        out.append((dev, PlugConfig(nr, typ, dev.host, name, fail_on=fail_on, totmann_s=30,
                                    auto_lauf=auto, auto_dauer=auto, nachlauf_s=20.0)))
    return out
