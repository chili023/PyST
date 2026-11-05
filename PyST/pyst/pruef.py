"""
Pruefungen fuer die Inbetriebnahme von Basis und Zange (Unterreiter „Prüfen“), ohne Oberflaeche.

Jede Pruefung liefert Check-Eintraege (Gruppe, Name, Ergebnis, Wert, Soll). Soll-Werte aus der
Inbetriebnahme-Checkliste (PyST-HW docs/INBETRIEBNAHME.md, Abschnitte 1.7, 2.6, 5.1, 5.2, 5.3).
Auswertung und Befehle sind getrennt: check_*() rechnen nur auf Antwortzeilen, run_*() sprechen mit dem Geraet.
"""
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from . import physics, zange
from .basis import (LOG_BOOT, LOG_MARK, LOG_SPARK, LOG_STATUS, LOG_SYNC, LogEntry, ZangeStatus, _f, _i, parse_lambda)
from .diag import parse_kv

OK, WARN, FEHLER, INFO = "ok", "warnung", "fehler", "info"
SYMBOL = {OK: "✓", WARN: "!", FEHLER: "✗", INFO: "·"}

BLITZ_MIN_US, BLITZ_MAX_US = 1, 25            # wie #BLITZ in der Zangen-Firmware
RSSI_WARN_DBM = -80.0
AKKU_WARN_PCT = 20.0


@dataclass
class Check:
    gruppe: str
    name: str
    ergebnis: str
    wert: str = ""
    soll: str = ""


def _merged(lines: List[str], prefix: str) -> Dict[str, str]:
    """Alle Schluessel=Wert-Felder der Zeilen mit diesem Anfang, in einem dict."""
    out: Dict[str, str] = {}
    for line in lines:
        if line.startswith(prefix):
            out.update(parse_kv(line))
    return out


def _range(gruppe: str, name: str, text: Optional[str], lo: float, hi: float, einheit: str) -> Check:
    soll = f"{lo:g} … {hi:g} {einheit}"
    x = _f(text)
    if x != x:
        return Check(gruppe, name, FEHLER, "keine Angabe", soll)
    return Check(gruppe, name, OK if lo <= x <= hi else FEHLER, f"{x:g} {einheit}", soll)


# ---------------------------------------------------------------------------------------------- Basis
def check_basis(caps: Dict[str, str], stat: List[str], net: List[str]) -> List[Check]:
    g = "Basis"
    out = [Check(g, "Firmware", OK if "basis" in caps.get("fw", "").lower() else FEHLER, caps.get("fw", "–"),
                 "PyST-Basis")]
    kv = _merged(stat, "#STAT;")
    if not kv:
        out.append(Check(g, "Zustand (#STAT)", FEHLER, "keine Antwort"))
        return out + check_net(net)
    clk = kv.get("clk", "")
    out.append(Check(g, "Takt", OK if clk == "HSE" else FEHLER, clk or "–", "HSE (Quarz Y1)"))
    reset = kv.get("reset", "")
    out.append(Check(g, "Letzter Reset", WARN if "watchdog" in reset else INFO, reset or "–"))
    out.append(Check(g, "USB", OK if kv.get("usb") == "1" else INFO, kv.get("usb", "–")))
    so = kv.get("sync_ofs", "")
    out.append(Check(g, "Sync-Ausgang", OK if so == "0" else WARN, f"sync_ofs={so}", "0"))
    # ab Basis 0.3.0
    if "auth_fehl" in kv:
        out.append(Check(g, "Anmeldung: Fehlversuche", OK if _i(kv["auth_fehl"], 0) == 0 else WARN,
                         f"{kv['auth_fehl']}, Sperre {kv.get('auth_sperre_s', '0')} s", "0"))
    if "link_tx_drop" in kv:
        lost = sum(_i(kv.get(k), 0) for k in ("link_tx_drop", "out_lost", "dma_te", "can_txvoll"))
        out.append(Check(g, "Verluste Link/Ausgabe/DMA/CAN", OK if lost == 0 else WARN,
                         " / ".join(kv.get(k, "–") for k in ("link_tx_drop", "out_lost", "dma_te", "can_txvoll")), "0"))
        out.append(Check(g, "Längster Messdurchlauf", INFO, f"{kv.get('mess_max_us', '–')} µs"))
    stacks = [(_i(v, 0), k[len("stack_"):]) for k, v in kv.items() if k.startswith("stack_")]
    if stacks:
        free, name = min(stacks)
        out.append(Check(g, "Kleinster Stack-Rest", INFO, f"{name}: {free} B"))
    out.append(_range(g, "12 V", kv.get("u12"), 10.0, 16.0, "V"))
    out.append(_range(g, "5 V Sensor", kv.get("u5s"), 4.75, 5.25, "V"))
    out.append(_range(g, "ADC-Referenz", kv.get("vref"), 2.45, 2.55, "V"))
    t = _f(kv.get("t_mcu"))
    out.append(Check(g, "Temperatur MCU", WARN if t > 70 else INFO, "–" if t != t else f"{t:.1f} °C", "< 70 °C"))
    out.append(Check(g, "Thermoelement-Wandler", OK if kv.get("ads") == "ok" else FEHLER, kv.get("ads", "–"), "ok"))
    klima = kv.get("klima", "")
    out.append(Check(g, "Klimasensor", {"ok": OK, "warnung": WARN}.get(klima, FEHLER),
                     f"{kv.get('bme', '–')}, {klima or '–'}", "ok"))
    for k, name in (("egt1_st", "Thermoelement 1"), ("egt2_st", "Thermoelement 2")):
        if k in kv:
            out.append(Check(g, name, INFO, kv[k]))
    wb = kv.get("wb", "")
    out.append(Check(g, "Wideband", INFO, wb or "–", "ohne F042-Firmware: reset, can_err zählt hoch"))
    lam = parse_lambda(kv)
    if lam.known:
        warn = lam.low_voltage or lam.grund == "gesperrt_watchdog"
        out.append(Check(g, "Lambda-Heizung", WARN if warn else INFO, lam.summary.replace("Lambda ", "", 1),
                         "aus, bereit oder heizt"))
    return out + check_net(net)


def check_klima(werte: Optional[Tuple[float, float, float]]) -> List[Check]:
    """Messwerte des Klimasensors ('e'). Genau 20,0 °C / 1013,25 mbar sind die Normwerte ohne Sensor."""
    g = "Klima"
    if werte is None:
        return [Check(g, "Messwerte", FEHLER, "keine Antwort")]
    t, p, h = werte
    if abs(t - 20.0) < 0.05 and abs(p - 1013.25) < 0.01:
        return [Check(g, "Messwerte", FEHLER, "Normwerte, Sensor liefert nichts", "echte Messwerte")]
    out = [_range(g, "Temperatur", f"{t:.1f}", -10.0, 50.0, "°C"),
           _range(g, "Luftdruck", f"{p:.1f}", 850.0, 1100.0, "mbar")]
    if h > 0:                                 # BMP280 misst keine Feuchte und meldet 0
        out.append(Check(g, "Luftfeuchte", INFO, f"{h:.1f} %"))
    return out


def check_net(lines: List[str]) -> List[Check]:
    g = "ESP / WLAN"
    kv = _merged(lines, "#NET;")
    if not kv:
        return [Check(g, "Netz (#NET)", FEHLER, "keine Antwort")]
    esp = kv.get("esp", "")
    out = [Check(g, "ESP-Link", OK if esp == "1" else FEHLER, esp, "1"),
           Check(g, "ESP-Firmware", INFO, kv.get("esp_fw", "–"))]
    n = _i(kv.get("esp_neustarts"), 0)
    out.append(Check(g, "ESP-Neustarts", OK if n == 0 else WARN, str(n), "0"))
    ssid = kv.get("ssid", "")
    out.append(Check(g, "WLAN-Name", OK if ssid else WARN, ssid or "nicht gesetzt",
                     "gesetzt (Reiter Zange · Netz · Steckdosen)"))
    out.append(Check(g, "WLAN-Modus", INFO, f"{kv.get('mode', '–')}, Kanal {kv.get('kanal', '–')}, {kv.get('ip', '–')}"))
    return out


def check_zangen(zangen: List[ZangeStatus]) -> List[Check]:
    """Zangen laut Basis (per Funk)."""
    g = "Zange (Funk)"
    real = [z for z in zangen if z.id]
    if not real:
        return [Check(g, "Kopplung", FEHLER, "keine gekoppelt", "gekoppelt: Koppeln …, an der Zange T1 + T2 3 s")]
    out = []
    for z in real:
        n = f"Zange {z.id}: " if len(real) > 1 else ""
        out.append(Check(g, n + "Verbindung", OK if z.connected else FEHLER,
                         "verbunden" if z.connected else "nicht erreichbar", "verbunden"))
        if not z.connected:
            continue
        rssi = z.rssi
        out.append(Check(g, n + "Empfang", WARN if rssi == rssi and rssi < RSSI_WARN_DBM else OK,
                         "–" if rssi != rssi else f"{rssi:.0f} dBm", f"> {RSSI_WARN_DBM:.0f} dBm"))
        pct = z.akku_pct
        out.append(Check(g, n + "Akku", WARN if pct == pct and pct < AKKU_WARN_PCT else OK,
                         "–" if pct != pct else f"{z.akku_mv / 1000:.2f} V, {pct:.0f} %"))
        out.append(Check(g, n + "USB an der Zange", WARN if z.vbus else OK, "steckt" if z.vbus else "frei",
                         "frei (im Lauf nie USB)"))
        out.append(Check(g, n + "Funken", INFO, str(z.funken)))
        if z.sync_ok:
            out.append(Check(g, n + "Zeitsync", OK, f"±{z.sync_us:.0f} µs", "0 … 1000 µs"))
        elif z.funken:
            out.append(Check(g, n + "Zeitsync", FEHLER, "kein Sync trotz Funken", "0 … 1000 µs"))
        else:
            out.append(Check(g, n + "Zeitsync", INFO, "braucht Funken", "0 … 1000 µs"))
    return out


# ---------------------------------------------------------------------------------------------- Zange per USB
def check_zange_usb(caps: Dict[str, str], lines: List[str], stat: List[str]) -> List[Check]:
    g = "Zange (USB)"
    out = [Check(g, "Firmware", OK if "zange" in caps.get("fw", "").lower() else FEHLER, caps.get("fw", "–"),
                 "PyST-Zange")]
    kv = _merged(lines, "#ZANGE;")
    if not kv:
        out.append(Check(g, "Zustand (#ZANGE)", FEHLER, "keine Antwort"))
        return out
    out.append(Check(g, "USB-Spannung", OK if kv.get("vbus") == "1" else FEHLER, kv.get("vbus", "–"), "1"))
    out.append(Check(g, "Akku", INFO, f"{_f(kv.get('akku_mv')) / 1000:.2f} V, {kv.get('akku_pct', '–')} %",
                     "bei USB ist das die Ladespannung"))
    out.append(Check(g, "Empfindlichkeit RV1", INFO, f"{kv.get('empf_mv', '–')} mV", "0 … 1100 mV"))
    gek = kv.get("gekoppelt", "")
    out.append(Check(g, "Gekoppelt", OK if gek == "1" else INFO, gek or "–"))
    out.append(Check(g, "Sync mit Basis", OK if kv.get("sync") == "1" else INFO, kv.get("sync", "–")))
    s = _merged(stat, "#STAT;")
    if not s:
        out.append(Check(g, "Zähler (#STAT)", FEHLER, "keine Antwort"))
        return out
    out.append(Check(g, "Brownouts", OK if _i(s.get("brownouts"), 0) == 0 else WARN, s.get("brownouts", "–"), "0"))
    out.append(Check(g, "ISR-Verlust", OK if _i(s.get("isr_verlust"), 0) == 0 else WARN, s.get("isr_verlust", "–"), "0"))
    out.append(Check(g, "Funk-Sendefehler", OK if _i(s.get("tx_fehler"), 0) == 0 else WARN, s.get("tx_fehler", "–"), "0"))
    out.append(Check(g, "Beacons", INFO, s.get("beacons", "–")))
    out.append(Check(g, "Funken / verworfen", INFO, f"{s.get('funken', '–')} / {s.get('verworfen', '–')}"))
    # ab Zange 0.3.0
    if "rmt_funken" in s:
        out.append(Check(g, "Funken ISR / RMT", INFO, f"{s.get('isr_funken', '–')} / {s['rmt_funken']}",
                         "gleich bis rund 169 Funken/s"))
    if "log_verlust" in s:
        out.append(Check(g, "Log-Verlust", OK if _i(s["log_verlust"], 0) == 0 else WARN, s["log_verlust"], "0"))
    if "heap_min" in s:
        out.append(Check(g, "Heap frei / kleinster", INFO, f"{s.get('heap_frei', '–')} / {s['heap_min']} B"))
    out.append(Check(g, "Letzter Reset", INFO, s.get("reset", "–")))
    return out


def check_blitz(reply: List[str], us: int, vorher: int, nachher: int) -> List[Check]:
    g = "Blitz"
    if not reply:
        return [Check(g, "Einzelblitz", FEHLER, "keine Antwort")]
    last = reply[-1]
    if not last.startswith("#OK"):
        why = last.split(";")[-1]
        hint = {"aktiv": "Blitzmodus an der Zange ausschalten", "zu schnell": "mindestens 200 ms Abstand"}
        return [Check(g, "Einzelblitz", WARN if why in hint else FEHLER, why, hint.get(why, ""))]
    return [Check(g, "Einzelblitz", OK if nachher == vorher + 1 else WARN, f"{us} µs, Zähler {vorher} → {nachher}",
                  "Zähler +1; ob es blitzt, zeigt das Oszi")]


def check_funken(n: int, dt: float, rej: int, soll_hz: float) -> List[Check]:
    g = "Funken"
    hz = n / dt if dt > 0 else 0.0
    if n == 0:
        return [Check(g, "Funken gezählt", FEHLER, f"0 in {dt:.1f} s", "Signal an der Zange?")]
    out = [Check(g, "Funken gezählt", OK, f"{n} in {dt:.1f} s")]
    if soll_hz > 0:
        d = abs(hz - soll_hz) / soll_hz
        out.append(Check(g, "Frequenz", OK if d <= 0.02 else FEHLER, f"{hz:.2f} Hz", f"{soll_hz:g} Hz ± 2 %"))
    else:
        out.append(Check(g, "Frequenz", INFO, f"{hz:.2f} Hz = {hz * 60:.0f} 1/min"))
    if rej >= 0:
        out.append(Check(g, "Verworfen", OK if rej == 0 else WARN, str(rej), "0"))
    return out


def check_log(entries: List[LogEntry]) -> List[Check]:
    """Log der Zange: Arten bekannt, Funken lesbar, Neustarts erkannt."""
    g = "Log"
    if not entries:
        return [Check(g, "Einträge", INFO, "Log ist leer")]
    known = (LOG_SPARK, LOG_MARK, LOG_SYNC, LOG_STATUS, LOG_BOOT)
    fremd = sorted({e.art for e in entries if e.art not in known})
    out = [Check(g, "Einträge", OK, str(len(entries))),
           Check(g, "Zeilenarten", FEHLER if fremd else OK, ", ".join(fremd) or "alle bekannt",
                 "F M Y S B (sonst passt die Firmware nicht zu PyST)")]
    res = zange.log_to_rows(entries, physics.DynoParams())
    out.append(Check(g, "Funken / verworfen", INFO, f"{res['sparks']} / {res['rejected']}"))
    out.append(Check(g, "Sitzungen", INFO, str(max(1, sum(1 for e in entries if e.art == LOG_BOOT)))))
    out.append(Check(g, "Marker", INFO, str(len(res["markers"]))))
    return out


# ---------------------------------------------------------------------------------------------- Befehle
def _stat_done(last: str):
    return lambda line: line.startswith(last) or line.startswith("#ERR")


def read_stat_zange(link) -> List[str]:
    return link.command("#STAT", timeout=1.0, done=_stat_done("#STAT;tx_ok"))


def blitz_count(link) -> int:
    return _i(_merged(read_stat_zange(link), "#STAT;").get("blitze"), 0)


def spark_count(link) -> Tuple[int, int]:
    """Funken und verworfene Funken; verworfen = -1, wenn das Geraet es nicht meldet (Basis)."""
    if link.is_zange_usb:
        kv = _merged(read_stat_zange(link), "#STAT;")
        return _i(kv.get("funken"), 0), _i(kv.get("verworfen"), 0)
    z = link.zange_status()
    return (z.funken if z else 0), -1


def read_klima(link, aufnahme: bool) -> List[Check]:
    """'e' stoppt die Ausgabe: im Ruhezustand kurz pausieren, waehrend einer Aufnahme nicht anfassen."""
    lief = link.streaming()
    if lief and aufnahme:
        return [Check("Klima", "Messwerte", INFO, "Aufnahme läuft, nicht geprüft")]
    werte = link.climate()
    if lief:
        link.start()
    return check_klima(werte)


def run_all(link, aufnahme: bool = False) -> List[Check]:
    if link.is_zange_usb:
        lines = link.command("#ZANGE", timeout=1.0, done=_stat_done("#ZANGE;rec"))
        return check_zange_usb(link.caps, lines, read_stat_zange(link))
    if link.is_basis:
        stat = link.command("#STAT", timeout=1.0, done=_stat_done("#STAT;u12"))
        net = link.command("#NET", timeout=1.0, done=_stat_done("#NET;"))
        zangen = link.zangen_status()
        klima = read_klima(link, aufnahme)
        return check_basis(link.caps, stat, net) + klima + check_zangen(zangen)
    return [Check("Gerät", "Art", WARN, link.caps.get("fw", "unbekannt"), "PyST-Basis oder Zange per USB")]


def run_funken(link, seconds: float, soll_hz: float) -> List[Check]:
    n0, r0 = spark_count(link)
    t0 = time.time()
    time.sleep(seconds)
    n1, r1 = spark_count(link)
    out = check_funken(n1 - n0, time.time() - t0, (r1 - r0) if r0 >= 0 else -1, soll_hz)
    if not link.is_zange_usb:
        out += [c for c in check_zangen(link.zangen_status()) if c.name.endswith("Zeitsync")]
    return out


def run_blitz(link, us: int) -> List[Check]:
    us = max(BLITZ_MIN_US, min(BLITZ_MAX_US, int(us)))
    vorher = blitz_count(link)
    reply = link.command(f"#BLITZ {us}", timeout=1.0)
    return check_blitz(reply, us, vorher, blitz_count(link))


def run_log(link) -> List[Check]:
    return check_log(link.log_read_all())


def protokoll(checks: List[Check], kopf: str = "") -> str:
    lines = [kopf] if kopf else []
    for c in checks:
        soll = f"  (Soll: {c.soll})" if c.soll else ""
        lines.append(f"{SYMBOL[c.ergebnis]} {c.gruppe} – {c.name}: {c.wert}{soll}")
    return "\n".join(lines)
