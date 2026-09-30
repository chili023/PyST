"""
PyST – Kommandozeile.

  python -m pyst                       Oberflaeche starten
  python -m pyst --sim                 Oberflaeche mit Simulator (ohne Hardware)
  python -m pyst ports                 serielle Ports anzeigen
  python -m pyst run --port SIM        Lauf ohne Oberflaeche (SIM = Simulator)
  python -m pyst run --port SIM:DATEI  gespeicherten Lauf (LabVIEW-XML) als Live-Lauf abspielen
  python -m pyst recalc DATEI.xml ...  LabVIEW-/PyST-Laeufe nachrechnen
  python -m pyst compare A B C ...     Laeufe tabellarisch vergleichen
"""
import argparse
import re
import sys
import time

from . import physics, storage
from .link import DynoLink, guess_port, list_serial_ports


def add_param_args(ap, defaults: physics.DynoParams):
    g = ap.add_argument_group("Pruefstand/Lauf (ueberschreibt Werte aus der Datei)")
    for name, help_ in [("inkr", "Rollengeber Inkr/U"), ("roll_circ", "Rollenumfang [m]"),
                        ("inertia", "Rollentraegheit [kgm2]"), ("imp", "Zuendimpulse/U"),
                        ("ratio", "Uebersetzung nKW/nRolle"), ("ma", "Gleitender Mittelwert"),
                        ("dq", "Differenzenquotient"), ("n_min", "n vom Gas / Startschwelle [1/min]"),
                        ("n_stop", "n Stop [1/min], 0 = aus"), ("m0", "Verlustmoment M0 [Nm]"),
                        ("m1", "Verlustmoment M1 [Nm]"), ("n1", "Rollendrehzahl n1 [1/min]"),
                        ("temp_c", "Lufttemperatur [degC]"), ("p_mbar", "Luftdruck [mbar]")]:
        g.add_argument("--" + name.replace("_", "-"), dest=name, type=float, default=None,
                       help=f"{help_} (Standard {getattr(defaults, name)})")


def apply_overrides(p: physics.DynoParams, args) -> physics.DynoParams:
    for k in physics.DynoParams.__dataclass_fields__:
        v = getattr(args, k, None)
        if v is not None:
            setattr(p, k, int(v) if k in ("ma", "dq") else v)
    return p


def cmd_ports(_args):
    for dev, desc in list_serial_ports():
        print(f"{dev:30s} {desc}")


def cmd_run(args):
    from .runner import DONE, ABORTED, RunController
    port = args.port or guess_port()
    if not port:
        sys.exit("Kein Port gefunden. --port angeben oder --port SIM fuer den Simulator.")
    link = DynoLink(port)
    # Beim Abspielen einer Datei (SIM:<Datei>) deren Pruefstandsparameter verwenden
    base = physics.DynoParams(**{**link.replay.params.__dict__, "n_stop": 0.0}) if link.replay else physics.DynoParams()
    p = apply_overrides(base, args)
    _check_or_exit(p)
    print(f"Port {port}")
    print(link.info() or "(keine Info-Antwort)")
    ctrl = RunController(link, p, auto_climate=not args.no_climate)
    ctrl.start()
    print(ctrl.message)
    print("GO – Vollgas, sobald bereit. Strg+C bricht ab.")
    last_print = 0.0
    try:
        while ctrl.state not in (DONE, ABORTED):
            ctrl.poll()
            if time.time() - last_print > 0.5:
                last_print = time.time()
                lv = ctrl.live
                hint = "  >>> GAS WEG <<<" if ctrl.gas_off() else ""
                print(f"[{ctrl.state:12s}] Motor {lv.n_calc:6.0f} (gemessen {lv.n_meas:6.0f}) 1/min  "
                      f"{lv.v_kmh:5.1f} km/h  {lv.rate:5.1f} Hz  verloren {link.lost}{hint}")
            time.sleep(0.02)
    except KeyboardInterrupt:
        ctrl.abort()
    if ctrl.state == DONE and ctrl.result:
        print("\n" + ctrl.result.summary() + f"\nEnde: {ctrl.result.end_reason}")
        folder = storage.save_run(ctrl, vehicle=args.vehicle or "", base_dir=args.dir or "")
        print(f"Gespeichert: {folder}")
    else:
        print(ctrl.message)
    link.close()


def ref_from_name(name: str):
    m = re.search(r"_ ?(\d+,\d)_(\d+)(\.xml)?$", name)
    return (float(m.group(1).replace(",", ".")), int(m.group(2))) if m else None


def _check_or_exit(p):
    msg = physics.check_params(p)
    if msg:
        sys.exit("Fehler: " + msg + "  (z.B. --n-min 4000 angeben)")


def cmd_recalc(args):
    print(f"{'Datei':42s} {'Quelle':10s} {'PS':>6s} {'bei':>6s} {'Nm':>6s} {'bei':>6s} {'k':>6s}  Referenz(Name)")
    for path in args.files:
        run = storage.load_any(path)
        p = apply_overrides(run["params"], args)
        _check_or_exit(p)
        res = storage.recompute(run, p)
        ref = ref_from_name(run["name"])
        ref_s = f"{ref[0]:.1f} PS bei {ref[1]}" if ref else ""
        if res is None:
            print(f"{run['name'][:42]:42s} {run['source']:10s}  keine Auswertung moeglich")
            continue
        print(f"{run['name'][:42]:42s} {run['source']:10s} {res.p_max:6.2f} {res.n_pmax:6.0f} "
              f"{res.m_max:6.2f} {res.n_mmax:6.0f} {res.ka:6.3f}  {ref_s}")
        if args.verbose:
            print("   ", p)


def cmd_compare(args):
    """Alle Laeufe mit den Parametern des ersten Laufs rechnen. Die Filter werden dabei auf gleiche
    Glaettungszeit umgerechnet, weil MA/dq in Messpunkten zaehlen (20 Hz alt, 60 Hz neu)."""
    runs = [storage.load_any(f) for f in args.files]
    base = apply_overrides(runs[0]["params"], args)
    _check_or_exit(base)
    ref_rate = physics.sample_rate(runs[0]["dt"])
    print("Parameter des ersten Laufs fuer alle:\n  ", base)
    print(f"Filter gelten bei {ref_rate:.1f} Hz: MA {base.ma} = {base.ma / ref_rate:.2f} s, "
          f"dq {base.dq} = +-{base.dq / ref_rate:.2f} s" + ("" if not args.same_samples else "  (NICHT umgerechnet)"))
    print(f"{'Datei':42s} {'PS':>6s} {'bei':>6s} {'Nm':>6s} {'bei':>6s} {'Hz':>6s} {'MA/dq':>7s} {'Punkte':>6s}")
    for run in runs:
        rate = physics.sample_rate(run["dt"])
        q = physics.DynoParams(**{**base.__dict__, "temp_c": run["params"].temp_c, "p_mbar": run["params"].p_mbar})
        if not args.same_samples:
            q = physics.scale_filters(q, ref_rate, rate)
        res = storage.recompute(run, q)
        if res is None:
            print(f"{run['name'][:42]:42s} keine Auswertung moeglich")
            continue
        print(f"{run['name'][:42]:42s} {res.p_max:6.2f} {res.n_pmax:6.0f} {res.m_max:6.2f} {res.n_mmax:6.0f} "
              f"{rate:6.1f} {q.ma:3d}/{q.dq:<3d} {len(res.n):6d}")


COMMANDS = ("gui", "ports", "run", "recalc", "compare", "-h", "--help")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] not in COMMANDS:
        # Oberflaeche; erlaubt: --sim und Lauf-Dateien zum Vergleich
        from .gui import main as gui_main
        gui_main()
        return
    ap = argparse.ArgumentParser(prog="pyst", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("gui", help="Oberflaeche (Standard)")
    sub.add_parser("ports", help="serielle Ports anzeigen")
    r = sub.add_parser("run", help="Lauf ohne Oberflaeche")
    r.add_argument("--port", help="Port oder SIM")
    r.add_argument("--no-climate", action="store_true", help="keine Klimaabfrage")
    r.add_argument("--vehicle", help="Fahrzeugdaten")
    r.add_argument("--dir", help="Speicherordner")
    add_param_args(r, physics.DynoParams())
    rc = sub.add_parser("recalc", help="Laeufe nachrechnen")
    rc.add_argument("files", nargs="+")
    rc.add_argument("-v", "--verbose", action="store_true")
    add_param_args(rc, physics.DynoParams())
    cp = sub.add_parser("compare", help="Laeufe mit gleichen Parametern vergleichen")
    cp.add_argument("files", nargs="+")
    cp.add_argument("--same-samples", action="store_true",
                    help="MA/dq NICHT auf gleiche Zeit umrechnen (gleiche Anzahl Messpunkte wie LabVIEW)")
    add_param_args(cp, physics.DynoParams())
    args = ap.parse_args(argv)

    if args.cmd == "gui":
        from .gui import main as gui_main
        gui_main()
    elif args.cmd == "ports":
        cmd_ports(args)
    elif args.cmd == "run":
        cmd_run(args)
    elif args.cmd == "recalc":
        cmd_recalc(args)
    elif args.cmd == "compare":
        cmd_compare(args)


if __name__ == "__main__":
    main()
