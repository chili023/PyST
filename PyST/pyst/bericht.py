"""
PDF-Bericht von dem, was gerade im Diagramm zu sehen ist (report.export_pdf zeichnet ihn).

- Messen (Datei › PDF-Bericht, Knopf der Auswertung, Knopf „PDF-Bericht“ unten rechts in der Statusleiste): das
  Diagramm wie angezeigt (Achsgrenzen, Kurvenauswahl oben/unten, Baender), dazu je Zeile der Tabelle
    - der automatische Mittelwert („Ø 3 Läufe · Setup (automatisch)“, Band min–max, Liste der Quelllaeufe),
    - Mittelwert-Referenzen (Ø, Band, Quellen),
    - die angehakten Laeufe auf den Plaetzen (gesperrt/rollierend),
    - die eben gemessene, noch nicht gespeicherte Kurve.
- Auswertung (nur ueber den Knopf der Statusleiste, wenn der Reiter Auswertung offen ist): deren Felder (X-Achse,
  Kanaele je Feld) mit den dort angezeigten Laeufen und Mittelwerten.
Die Kopfzeile des Berichts nennt, woher das Diagramm kommt (DIAGRAMM_MESSEN / DIAGRAMM_AUSWERTUNG).
Reine Hilfen ueber dem Hauptfenster (win = gui.MainWindow), damit gui.py klein bleibt.
"""
import os
from typing import Dict, List, Optional

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

from . import physics, report, theme
from .channels import fault_text
from .db import SETUP_SUMMARY_KEYS, field_label
from .plots import LOWER_MARKS, run_color

MESSEN, AUSWERTUNG = "messen", "auswertung"
DIAGRAMM_TEXT = {MESSEN: "Diagramm aus Messen", AUSWERTUNG: "Diagramm aus der Auswertung"}
HINWEIS_MESSEN = ("Leistung nach DIN 70020 korrigiert (Radanteil; Verluste unkorrigiert wie LabVIEW).  "
                  "Mit Verlusten: Motor = Rad + Verluste (Herkunft unter Details).  Ø = Mittelwert, Fläche = "
                  "Band min–max (bzw. ± σ) der Quellläufe.")
HINWEIS_AUSWERTUNG = ("Auswertung: jeder Kanal ist für sich auf die Feldhöhe skaliert (Kanäle gleicher Einheit "
                      "teilen sich eine Skala).  Ø = Mittelwert mit Band.")


def _z(x: float, dez: int = 0) -> str:
    return f"{x:,.{dez}f}".replace(",", " ").replace(".", ",").replace(" ", ".")


def _setup_text(win, vid, sid) -> str:
    """„Fahrzeug / Setup“ und die Kurzwerte des Setups (zweite Zeile)."""
    veh = win.db.vehicle(vid) if vid else None
    setup = win.db.setup(sid) if sid else None
    kopf = " / ".join(x for x in (win.db.vehicle_name(veh) if veh else "", setup["name"] if setup else "") if x)
    details = ""
    if setup:
        details = ", ".join(f"{field_label(k).split(' [')[0]}: {setup[k]}" for k in SETUP_SUMMARY_KEYS
                            if setup.get(k))
    return kopf + ("\n" + details if details else "")


def _werte_text(row) -> str:
    """Setup-Werte des Laufs (Schnappschuss), z.B. „Hauptdüse 138 (vorher 135)“."""
    teile = []
    for w in row.setup_werte or []:
        if not w.get("wert"):
            continue
        t = f"{field_label(w['feld']).split(' [')[0]} {w['wert']}"
        if w.get("vorher") and w["vorher"] != w["wert"]:
            t += f" (vorher {w['vorher']})"
        teile.append(t)
    return ("Setup-Werte: " + ", ".join(teile)) if teile else ""


def _quellen_text(win, ids) -> str:
    namen = []
    for i in ids:
        r = win.db.run(i)
        if r is not None:
            namen.append(win._run_label(r).split("  ")[0])
    return ", ".join(namen)


def lauf_zeile(win, row, platz: str = "") -> Dict[str, str]:
    """Tabellenzeile eines Laufs mit Rohdaten (wie frueher gui.pdf_rows). platz: „Platz gesperrt“ …"""
    e = win._entry(row.id)
    p = e[3] if e else None
    res = e[1] if e else None
    if res is not None and res.verluste:
        ergebnis = (f"Motor {row.p_max:.1f} PS bei {row.n_pmax:.0f} 1/min\n"
                    f"Rad {res.p_rad_max:.1f} PS bei {res.n_prad_max:.0f} 1/min\n"
                    f"{row.m_max:.1f} Nm (Motor) bei {row.n_mmax:.0f} 1/min")
    else:
        ergebnis = f"{row.p_max:.1f} PS bei {row.n_pmax:.0f} 1/min\n{row.m_max:.1f} Nm bei {row.n_mmax:.0f} 1/min"
    if res is not None and res.rolle_kalibriert:
        ergebnis += f"\nReifen an Rolle {res.p_reifen_max:.1f} PS" + (
            f", Antrieb {res.antrieb_ps_pmax:.1f} PS" if res.verluste else "")
    werte = _werte_text(row)
    name = win._run_label(row) + (f"\n{row.notiz}" if row.notiz else "") + (f"\n{platz}" if platz else "")
    return {
        "farbe": run_color(row, theme.TAG),       # Bericht auf weissem Papier
        "name": name,
        "fahrzeug": _setup_text(win, row.vehicle_id, row.setup_id) + (f"\n{werte}" if werte else ""),
        "ergebnis": ergebnis,
        "klima": "" if p is None else
        f"{p.temp_c:.1f} °C, {p.p_mbar:.0f} mbar\nk = {physics.din70020(p.temp_c, p.p_mbar):.3f}",
        "details": ("" if p is None else
                    f"i = {p.ratio:.3f}, J = {p.inertia:.2f}\n{physics.filter_text(p, row.rate)}")
        + (f"\n{res.verlust_text()}" if res is not None and res.verluste else "")
        + (f"\n{res.rk_quelle or 'Rollen-Kalibrierung'}" if res is not None and res.rolle_kalibriert else "")
        + (f"\nMessfehler: {fault_text(row.fehler)}" if row.fehler else ""),
        "art": "lauf",
    }


def _mw_ergebnis(erg) -> str:
    return (f"Pmax {erg.p_max:.1f} PS bei {erg.n_pmax:.0f} 1/min\n"
            f"Mmax {erg.m_max:.1f} Nm bei {erg.n_mmax:.0f} 1/min")


def _band_name(art: str) -> str:
    return {"minmax": "Band min–max", "sigma": "Band ± σ"}.get(art, "ohne Band")


def mittelwert_zeile(win, row, erg, farbe: str, band: str = "minmax", platz: str = "") -> Dict[str, str]:
    """Tabellenzeile eines gespeicherten Mittelwert-Laufs (Referenz)."""
    werte = _werte_text(row)
    quellen = list(row.quellen or getattr(erg, "quellen", []) or [])
    return {
        "farbe": theme.laufarbe(farbe, theme.TAG),
        "name": f"{row.name}\nMittelwert (Referenz)" + (f"\n{row.notiz}" if row.notiz else "")
        + (f"\n{platz}" if platz else ""),
        "fahrzeug": _setup_text(win, row.vehicle_id, row.setup_id) + (f"\n{werte}" if werte else ""),
        "ergebnis": _mw_ergebnis(erg),
        "klima": "–",
        "details": f"Ø aus {len(quellen) or erg.anzahl} Läufen, {_band_name(band)}"
        + (f"\nQuellen: {_quellen_text(win, quellen)}" if quellen else ""),
        "art": "mittelwert",
    }


def auto_zeile(win) -> Optional[Dict[str, str]]:
    """Automatischer Mittelwert (Haken „Ø automatisch mitteln“), wenn er im Diagramm steht."""
    from .messen_page import AUTO_KEY
    info = getattr(win, "_auto", None)
    if info is None or AUTO_KEY not in win.plot.data:
        return None
    erg = info["erg"]
    return {
        "farbe": theme.laufarbe("@avg_color", theme.TAG),
        "name": info["titel"] + "\nnicht gespeichert",
        "fahrzeug": _setup_text(win, info["vid"], info["sid"]),
        "ergebnis": _mw_ergebnis(erg),
        "klima": "–",
        "details": f"Ø aus {len(info['ids'])} Läufen mit dem Setup des neuesten, Band min–max\n"
                   f"Quellen: {_quellen_text(win, info['ids'])}",
        "art": "auto",
    }


def live_zeile(win) -> Optional[Dict[str, str]]:
    """Eben gemessene, (noch) nicht gespeicherte Kurve im Diagramm."""
    from .messen_page import LIVE_KEY
    d = win.plot.data.get(LIVE_KEY)
    if d is None or not len(d.get("n", [])) or not np.isfinite(d["ps"]).any():
        return None
    jp, jm = int(np.nanargmax(d["ps"])), int(np.nanargmax(d["nm"]))
    p = win.page_settings.params()
    return {
        "farbe": theme.laufarbe("#000000", theme.TAG),     # Live-Kurve (auf Papier dunkel)
        "name": "Letzter Lauf\nnicht gespeichert",
        "fahrzeug": _setup_text(win, win.vehicle_combo.currentData(), win.setup_combo.currentData()),
        "ergebnis": f"{d['ps'][jp]:.1f} PS bei {d['n'][jp]:.0f} 1/min\n{d['nm'][jm]:.1f} Nm bei {d['n'][jm]:.0f} 1/min",
        "klima": f"{p.temp_c:.1f} °C, {p.p_mbar:.0f} mbar\nk = {physics.din70020(p.temp_c, p.p_mbar):.3f}",
        "details": f"i = {p.ratio:.3f}, J = {p.inertia:.2f}",
        "art": "live",
    }


def messen_zeilen(win) -> List[Dict[str, str]]:
    """Alles, was im Messen-Diagramm steht: Ø automatisch, Referenzen (Mittelwerte), Laeufe auf den Plaetzen
    (neueste zuerst), die ungespeicherte Kurve."""
    from .messen_page import band_art
    fest, _roll = getattr(win, "_plaetze", ([], []))
    laeufe = getattr(win, "_laeufe", {})
    im_diagramm = set(win.diagramm_laeufe())
    rows: List[Dict[str, str]] = []
    a = auto_zeile(win)
    if a is not None:
        rows.append(a)
    mws, echte = [], []
    for rid in im_diagramm:
        row = laeufe.get(rid) or win.db.run(rid)
        if row is None or not row.sichtbar:
            continue
        (mws if row.ist_mittelwert else echte).append(row)
    for row in sorted(mws, key=lambda r: r.datum, reverse=True):
        erg = win._mittelwert(row)
        if erg is not None:
            rows.append(mittelwert_zeile(win, row, erg, win._lauf_farbe(row), band_art(row),
                                         "Platz gesperrt" if row.id in fest else ""))
    for row in sorted(echte, key=lambda r: r.datum, reverse=True):
        rows.append(lauf_zeile(win, row, "Platz gesperrt" if row.id in fest else ""))
    lv = live_zeile(win)
    if lv is not None:
        rows.append(lv)
    return rows


def _grenzen_text(lim: Dict[str, float]) -> str:
    namen = {"n": ("n", "1/min"), "ps": ("P", "PS"), "nm": ("M", "Nm"), "lam": ("unten links", ""),
             "egt": ("unten rechts", "")}
    teile = []
    for k, (n, einheit) in namen.items():
        lo, hi = lim.get(f"{k}_lo", 0.0) or 0.0, lim.get(f"{k}_hi", 0.0) or 0.0
        if lo or hi:
            von = f"ab {_z(lo, 1 if abs(lo) < 10 else 0)}" if lo else ""
            bis = f"bis {_z(hi, 1 if abs(hi) < 10 else 0)}" if hi else ""
            teile.append(f"{n} {' '.join(x for x in (von, bis) if x)}" + (f" {einheit}" if einheit else ""))
    return ("Achsgrenzen fest: " + ", ".join(teile)) if teile else "Achsgrenzen automatisch"


def messen_angaben(win) -> str:
    """Kopfzeile unter dem Titel: Herkunft, Drehzahlbereich, Kurven oben/unten, Achsgrenzen, Fahrzeug/Setup."""
    plot = win.plot
    (x0, x1), _y = plot.top.pi.viewRange()
    oben = ["── Leistung [PS]", "╌╌ Drehmoment [Nm]"] + [f"{lab}" + (f" [{u}]" if u else "")
                                                       for _k, lab, u in plot.overlays]
    teile = [DIAGRAMM_TEXT[MESSEN], f"Drehzahl {_z(x0)}–{_z(x1)} 1/min", "oben: " + ", ".join(oben)]
    if plot.lower_visible:
        unten = []
        for seite in ("left", "right"):                # Linienart wie im Diagramm (mehrere je Seite)
            keys = plot.lower[seite]
            for i, k in enumerate(keys):
                marke = LOWER_MARKS[i % len(LOWER_MARKS)] + " " if len(keys) > 1 else ""
                unten.append(marke + plot.lower_title(k)[0])
        teile.append("unten: " + (", ".join(unten) or "–"))
    else:
        teile.append("unteres Diagramm ausgeblendet")
    teile.append(_grenzen_text(plot.limits))
    return "  ·  ".join(teile)


def auswertung_zeilen(win) -> List[Dict[str, str]]:
    v = win.viewer
    from .mittelwert_dialog import BAND_STANDARD
    rows = []
    for rid in v._runs_shown():
        row = v.rows.get(rid) or win.db.run(rid)
        if row is None:
            continue
        if row.ist_mittelwert:
            info = v.mw_info.get(rid)
            if info is not None:
                z = mittelwert_zeile(win, row, info["res"], v._farbe(rid), info.get("band", BAND_STANDARD))
                z["farbe"] = theme.laufarbe(v._farbe(rid), theme.TAG)
                rows.append(z)
        else:
            z = lauf_zeile(win, row)
            z["farbe"] = theme.laufarbe(v._farbe(rid), theme.TAG)
            rows.append(z)
    return rows


def auswertung_angaben(win) -> str:
    v = win.viewer
    teile = [DIAGRAMM_TEXT[AUSWERTUNG], f"X-Achse: {v.xaxis.currentText()}"]
    if v.only_seg.isChecked():
        teile.append("nur Kurvenbereich")
    for p in range(v.panes_spin.value()):
        teile.append(f"Feld {p + 1}: " + (", ".join(v._selected_channels(p)) or "–"))
    return "  ·  ".join(teile)


def auswertung_bild(win) -> QtGui.QImage:
    """Felder der Auswertung als Bild, immer im Tagmodus (weisses Papier)."""
    v = win.viewer
    m = theme.manager()
    alt = m.aktiv
    if alt != theme.TAG:
        m.aktiv = theme.TAG                   # nur fuer dieses Bild
        v._thema(theme.TAG)
    try:
        return report.render_plot(v.glw.scene())
    finally:
        if alt != theme.TAG:
            m.aktiv = alt
            v._thema(alt)


def titel(win) -> str:
    return win.vehicle_combo.currentText() if win.vehicle_combo.currentData() else "Leistungsmessung"


def daten(win, quelle: str = MESSEN):
    """(Bild, Titel, Untertitel, Zeilen, Hinweise) des Berichts; Zeilen leer = nichts im Diagramm."""
    if quelle == AUSWERTUNG:
        rows = auswertung_zeilen(win)
        if not rows:
            return None, titel(win), "", [], []
        return auswertung_bild(win), titel(win), auswertung_angaben(win), rows, [HINWEIS_AUSWERTUNG]
    rows = messen_zeilen(win)
    if not rows:
        return None, titel(win), "", [], []
    return win.plot.render_image(), titel(win), messen_angaben(win), rows, [HINWEIS_MESSEN]


def erzeugen(win, path: str = "", quelle: str = MESSEN) -> Optional[str]:
    """PDF-Bericht schreiben. Ohne path: Speicherdialog (Vorschlag Leistungsmessung.pdf), danach oeffnen.
    -> Pfad bzw. None (nichts im Diagramm, abgebrochen)."""
    rows = auswertung_zeilen(win) if quelle == AUSWERTUNG else messen_zeilen(win)
    if not rows:
        wo = "in der Auswertung" if quelle == AUSWERTUNG else "im Diagramm"
        QtWidgets.QMessageBox.information(win, "PDF-Bericht", f"Keine Läufe {wo} – bitte in der Liste anhaken.")
        return None
    open_after = not path
    if not path:
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            win, "PDF-Bericht", os.path.join(os.path.expanduser("~"), "Leistungsmessung.pdf"), "PDF (*.pdf)")
        if not path:
            return None
    bild, tit, unter, rows, hinweise = daten(win, quelle)
    report.export_pdf(path, bild, tit, rows, hinweise, untertitel=unter)
    win.statusBar().showMessage(f"PDF-Bericht gespeichert ({DIAGRAMM_TEXT[quelle]}): {path}", 8000)
    if open_after:
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(path))
    return path
