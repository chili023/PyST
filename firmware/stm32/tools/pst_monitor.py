#!/usr/bin/env python3
"""
PST-Monitor: Messelektronik ohne LabVIEW testen (Mac, Linux, Windows).

    python3 -m pip install pyserial
    python3 tools/pst_monitor.py              # Port automatisch suchen
    python3 tools/pst_monitor.py /dev/cu.usbmodem1103 --imp 1 --inkr 41 --umfang 1.57

Ablauf wie LabVIEW 3.x: 'v' (Info), 'e' (Klimadaten), 'm' (Messung starten),
dann Telegramme lesen und als Drehzahl anzeigen. Strg+C beendet und sendet 's'.
"""
import argparse
import sys
import time

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    sys.exit("pyserial fehlt:  python3 -m pip install pyserial")


def find_port():
    for p in list_ports.comports():
        text = f"{p.device} {p.description} {p.manufacturer or ''}".lower()
        if "stlink" in text or "st-link" in text or "stm" in text or "usbmodem" in text:
            return p.device
    ports = [p.device for p in list_ports.comports()]
    sys.exit("Kein ST-LINK-Port gefunden. Vorhanden: " + (", ".join(ports) or "keine"))


def read_for(ser, seconds):
    end = time.time() + seconds
    data = b""
    while time.time() < end:
        data += ser.read(ser.in_waiting or 1)
    return data.decode("latin-1", "replace")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("port", nargs="?", help="serieller Port (Standard: automatisch)")
    ap.add_argument("--imp", type=float, default=1.0, help="Zuendimpulse pro Kurbelwellenumdrehung")
    ap.add_argument("--inkr", type=float, default=41.0, help="Rollengeber Inkremente pro Umdrehung")
    ap.add_argument("--umfang", type=float, default=1.57, help="Rollenumfang [m]")
    args = ap.parse_args()

    port = args.port or find_port()
    print(f"Port: {port}")
    with serial.Serial(port, 115200, timeout=0.05) as ser:
        ser.reset_input_buffer()
        ser.write(b"s\n")                 # evtl. laufende Messung beenden
        time.sleep(0.2)
        ser.reset_input_buffer()

        ser.write(b"v\n")
        print(read_for(ser, 0.5).strip())

        ser.write(b"e\n")
        env = read_for(ser, 0.5).strip()
        try:
            t, p, h = env.split(";")[:3]
            print(f"\nKlima: {float(t):.1f} degC  {float(p) / 100:.1f} mbar  {float(h):.1f} %")
        except ValueError:
            print(f"\nKlima: unerwartete Antwort {env!r}")

        print("\nZyklus  Rate[Hz]  Motor[1/min]  Rolle[1/min]  v[km/h]  EGT1  AFR  EGT2")
        ser.write(b"m\n")
        buf = b""
        last_cycle = None
        lost = 0
        try:
            while True:
                buf += ser.read(ser.in_waiting or 1)
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    f = line.decode("latin-1").strip().split(";")
                    if len(f) < 4:
                        continue
                    cyc, rate, ign, roll = int(f[0]), float(f[1]), float(f[2]), float(f[3])
                    if last_cycle is not None and cyc != (last_cycle + 1) & 0xFFFF:
                        lost += 1
                    last_cycle = cyc
                    n_mot = ign * 60.0 / args.imp
                    n_roll = roll * 60.0 / args.inkr
                    v = n_roll / 60.0 * args.umfang * 3.6
                    extra = "  ".join(f[4:7])
                    if cyc % 6 == 0:  # ca. 10 Zeilen pro Sekunde
                        print(f"{cyc:6d}  {rate:8.2f}  {n_mot:12.0f}  {n_roll:12.1f}  {v:7.1f}  {extra}"
                              + (f"  (verloren: {lost})" if lost else ""))
        except KeyboardInterrupt:
            ser.write(b"s\n")
            print("\nMessung gestoppt.")


if __name__ == "__main__":
    main()
