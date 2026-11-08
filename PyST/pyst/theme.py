"""
Tag- und Nachtmodus: Farb-Tokens, Palette, Stylesheet der Anwendung, Laufarben je Thema.

Bedienung: Menue Ansicht › Darstellung (Tag / Nacht / Automatisch) und der Umschalter rechts in der Statusleiste.
"Automatisch" folgt dem Farbschema des Systems (Qt styleHints().colorScheme(), unbekannt = Tag).

Benutzung in der Oberflaeche:
  from . import theme
  theme.farbe("text")                 Farbe eines Tokens im aktiven Thema ("#rrggbb")
  theme.tokens()                      alle Tokens des aktiven Themas (dict)
  theme.manager().geaendert           Signal(str) nach jedem Wechsel, Argument = aktives Thema "tag"/"nacht"
  theme.bei_wechsel(widget, fn)       fn(thema) jetzt und nach jedem Wechsel aufrufen (bis widget geloescht ist)
  theme.rolle(widget, "neben")        Textrolle per Eigenschaft (Stylesheet der Anwendung faerbt, auch nach Wechsel)
  theme.stufe(widget, "ok")           Zustandsfarbe als Schrift: ok | warn | err | "" (normal)
  theme.status_farben("warn")         (Hintergrund, Schrift, Symbol) fuer farbige Zustandsflaechen
  theme.laufarbe("#bcbd22")           Kurvenfarbe auf >= 3,2:1 gegen die Diagrammflaeche (gleicher Farbton)

Die Tokens stammen aus dem Entwurf (Doku/Entwurf_Oberflaeche_2026-11-08, skripte/theme.py, Runde 3).
Kontrast-Sollwerte (WCAG 2.x, KONTRAST_PAARE, geprueft in tests/test_theme.py): Fliesstext >= 7:1,
Nebentext/Achsen >= 4,5:1, Bedienelemente/Rahmen/Kurven >= 3:1.
"""
import colorsys
import os
import tempfile
from functools import lru_cache
from typing import Callable, Dict, Optional, Tuple

from PySide6 import QtCore, QtGui, QtWidgets

TAG, NACHT, AUTO = "tag", "nacht", "auto"
WAHLEN = (TAG, NACHT, AUTO)
WAHL_TEXT = {TAG: "Tag", NACHT: "Nacht", AUTO: "Automatisch"}
STANDARD_WAHL = AUTO

# Schrift (Pixel): Fliesstext 13, Achsen 12, Achstitel 13 fett, Werte in Kacheln 15-30 fett
SCHRIFT_PX = 13
ACHSE_PX = 12

# Token -> Farbe. Hintergruende: win (Fenster), panel (Kacheln/Gruppen), field (Eingabe), plot (Diagrammflaeche)
THEMES: Dict[str, Dict[str, str]] = {
    NACHT: dict(
        win="#1c1c1f", panel="#26262b", field="#121214", plot="#141417", menu="#2c2c32",
        text="#f2f2f4", text2="#c9c9d0", value="#ffffff", dim="#b4b4bc",
        border="#7a7a84", border_strong="#a8a8b2",
        accent="#174ea6", accent_text="#ffffff", accent_border="#8ab4f8", sel_bg="#174ea6",
        plot_axis="#c9c9d0", plot_grid="#3a3a42", plot_frame="#8a8a94", cursor="#e0e0e6",
        live_curve="#ffffff",
        p_color="#ff8a80", m_color="#82b1ff", lam_color="#81c784", egt_color="#ffb74d",
        ok_bg="#1b5e20", ok_text="#ffffff", warn_bg="#f9c22e", warn_text="#1a1a1a",
        err_bg="#a31515", err_text="#ffffff",
        zone_ok="#4caf50", zone_warn="#f9c22e", zone_err="#ef5350", zone_cold="#64b5f6", track="#3a3a42",
        changed_bg="#f9c22e", changed_text="#1a1a1a", strip_changed_bg="#3a3115",
        tab_sel="#2f2f36", input_text="#ffffff", alt_row="#202024",
        avg_color="#e1a6ef", lock_bg="#2b3242",
        # Ergaenzungen Stufe 1: Zustand "aus" (grau), Zustandsschrift auf Fenster/Kachel, Cursorwerte-Hintergrund
        off_bg="#4a4a52", off_text="#ffffff",
        ok_fg="#81c784", warn_fg="#ffb74d", err_fg="#ff8a80", info_fg="#8ab4f8",
        cursor_bg="#3a3115",
    ),
    TAG: dict(
        win="#f3f3f5", panel="#ffffff", field="#ffffff", plot="#ffffff", menu="#ffffff",
        text="#141416", text2="#4a4a52", value="#000000", dim="#5c5c66",
        border="#7c7c86", border_strong="#5c5c66",
        accent="#174ea6", accent_text="#ffffff", accent_border="#174ea6", sel_bg="#174ea6",
        plot_axis="#3c3c44", plot_grid="#dcdce2", plot_frame="#6e6e78", cursor="#2a2a30",
        live_curve="#000000",
        p_color="#b71c1c", m_color="#0d47a1", lam_color="#1b5e20", egt_color="#a84300",
        ok_bg="#1b5e20", ok_text="#ffffff", warn_bg="#f9c22e", warn_text="#1a1a1a",
        err_bg="#a31515", err_text="#ffffff",
        zone_ok="#2e7d32", zone_warn="#c77c00", zone_err="#c62828", zone_cold="#1565c0", track="#e2e2e8",
        changed_bg="#f9c22e", changed_text="#1a1a1a", strip_changed_bg="#fff4cc",
        tab_sel="#ffffff", input_text="#000000", alt_row="#f4f4f7",
        avg_color="#7b1fa2", lock_bg="#e3ecfa",
        off_bg="#4a4a52", off_text="#ffffff",
        ok_fg="#1b5e20", warn_fg="#8a4b00", err_fg="#b71c1c", info_fg="#174ea6",
        cursor_bg="#fff4cc",
    ),
}

# Zustaende der farbigen Flaechen (Statusleiste, Zustandsfeld): Stufe -> (Hintergrund, Schrift, Symbol)
GUT, WARNUNG, FEHLER, AUS = "ok", "warn", "err", "aus"
STUFEN = (GUT, WARNUNG, FEHLER, AUS)
_STATUS = {GUT: ("ok_bg", "ok_text", "✔"), WARNUNG: ("warn_bg", "warn_text", "⚠"),
           FEHLER: ("err_bg", "err_text", "✖"), AUS: ("off_bg", "off_text", "–")}
# Zonen der Balken (Kacheln): Stufe -> Token
ZONEN = {"ok": "zone_ok", "warn": "zone_warn", "err": "zone_err", "cold": "zone_cold"}

# (Token Vordergrund, Token Hintergrund, Rolle, Mindestwert) – alle Paare werden im Test fuer beide Themen geprueft
KONTRAST_PAARE = [
    ("text", "win", "Fließtext auf Fenster", 7.0),
    ("text", "panel", "Fließtext auf Kachel/Gruppe", 7.0),
    ("input_text", "field", "Text in Eingabefeld", 7.0),
    ("value", "panel", "Messwert (groß) auf Kachel", 7.0),
    ("text2", "win", "Nebenschrift auf Fenster", 4.5),
    ("text2", "panel", "Nebenschrift auf Kachel", 4.5),
    ("text2", "field", "Nebenschrift in Tabellen", 4.5),
    ("dim", "panel", "„–“ / nicht verfügbar", 4.5),
    ("dim", "win", "abgeschaltete Bedienelemente", 4.5),
    ("plot_axis", "plot", "Achsbeschriftung", 4.5),
    ("p_color", "plot", "Achstitel Leistung", 4.5),
    ("m_color", "plot", "Achstitel Drehmoment", 4.5),
    ("lam_color", "plot", "Achstitel Lambda", 4.5),
    ("egt_color", "plot", "Achstitel EGT", 4.5),
    ("p_color", "panel", "Pmax-Zahl auf Kachel", 4.5),
    ("m_color", "panel", "Mmax-Zahl auf Kachel", 4.5),
    ("accent_text", "accent", "Text Primärknopf", 7.0),
    ("accent_text", "sel_bg", "Text in Auswahl", 7.0),
    ("ok_text", "ok_bg", "Status grün", 7.0),
    ("warn_text", "warn_bg", "Status gelb", 7.0),
    ("err_text", "err_bg", "Status rot", 7.0),
    ("off_text", "off_bg", "Status aus (grau)", 7.0),
    ("changed_text", "changed_bg", "geänderter Setup-Wert", 7.0),
    ("text", "strip_changed_bg", "Text auf Setup-Zeile (geändert)", 7.0),
    ("text", "cursor_bg", "Text auf Cursorwerte-Hintergrund", 7.0),
    ("text", "menu", "Menütext", 7.0),
    ("text", "tab_sel", "Text gewählter Reiter", 7.0),
    ("text", "alt_row", "Text in Wechselzeile", 7.0),
    ("ok_fg", "win", "Zustandsschrift gut auf Fenster", 4.5),
    ("warn_fg", "win", "Zustandsschrift Warnung auf Fenster", 4.5),
    ("err_fg", "win", "Zustandsschrift Fehler auf Fenster", 4.5),
    ("info_fg", "win", "Hinweisschrift auf Fenster", 4.5),
    ("ok_fg", "panel", "Zustandsschrift gut auf Kachel", 4.5),
    ("warn_fg", "panel", "Zustandsschrift Warnung auf Kachel", 4.5),
    ("err_fg", "panel", "Zustandsschrift Fehler auf Kachel", 4.5),
    ("border", "win", "Rahmen/Bedienelement auf Fenster", 3.0),
    ("border", "panel", "Rahmen auf Kachel", 3.0),
    ("border", "field", "Rahmen Eingabefeld", 3.0),
    ("plot_frame", "plot", "Diagrammrahmen", 3.0),
    ("cursor", "plot", "Cursorlinie", 3.0),
    ("live_curve", "plot", "Live-Kurve", 3.0),
    ("zone_ok", "panel", "Balken grün", 3.0),
    ("zone_warn", "panel", "Balken gelb", 3.0),
    ("zone_err", "panel", "Balken rot", 3.0),
    ("zone_cold", "panel", "Balken kalt (blau)", 3.0),
    ("accent_border", "win", "Overlay-Rahmen", 3.0),
    ("avg_color", "plot", "Mittelwert-Kurve Ø", 3.0),
    ("text", "lock_bg", "Text gesperrter Lauf (Zeile)", 7.0),
]
LAUF_KONTRAST = 3.2           # Laufarben gegen die Diagrammflaeche


# =============================================================================================== Rechnen
def _lin(c: float) -> float:
    c /= 255
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def _rgb(h: str) -> Tuple[int, int, int]:
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def leuchtdichte(h: str) -> float:
    r, g, b = _rgb(h)
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def kontrast(a: str, b: str) -> float:
    """Kontrastverhaeltnis nach WCAG 2.x (1 … 21)."""
    la, lb = leuchtdichte(a), leuchtdichte(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


@lru_cache(maxsize=512)
def anpassen(farbe: str, hintergrund: str, ziel: float = LAUF_KONTRAST) -> str:
    """Gleiche Farbe (Farbton/Saettigung), Helligkeit so weit verschoben, bis der Kontrast >= ziel ist."""
    r, g, b = (x / 255 for x in _rgb(farbe))
    hh, ll, ss = colorsys.rgb_to_hls(r, g, b)
    dunkler = leuchtdichte(hintergrund) > 0.4
    c = "#%02x%02x%02x" % _rgb(farbe)
    for _ in range(101):
        rr, gg, bb = colorsys.hls_to_rgb(hh, ll, ss)
        c = "#%02x%02x%02x" % (round(rr * 255), round(gg * 255), round(bb * 255))
        if kontrast(c, hintergrund) >= ziel:
            return c
        ll = min(max(ll - 0.01 if dunkler else ll + 0.01, 0.0), 1.0)
    return c


def kontrast_tabelle(thema: str):
    """[(Rolle, Vordergrund, Hintergrund, Kontrast, Soll)] aller KONTRAST_PAARE."""
    T = THEMES[thema]
    return [(rolle, T[fg], T[bg], kontrast(T[fg], T[bg]), soll) for fg, bg, rolle, soll in KONTRAST_PAARE]


# =============================================================================================== Abfragen
def _thema(thema: Optional[str]) -> str:
    return thema if thema in THEMES else aktiv()


def tokens(thema: Optional[str] = None) -> Dict[str, str]:
    return THEMES[_thema(thema)]


def farbe(token: str, thema: Optional[str] = None) -> str:
    return THEMES[_thema(thema)][token]


def laufarbe(basis: str, thema: Optional[str] = None) -> str:
    """Kurvenfarbe eines Laufs im Thema: Basis aus plots.RUN_COLORS (oder selbst gewaehlt), Helligkeit
    auf >= 3,2:1 gegen die Diagrammflaeche verschoben. Tag: Gelb wird Oliv, Cyan wird Petrol.
    Sonderfaelle: "@<token>" = Token des Themas (z.B. "@avg_color" fuer den Mittelwert-Lauf, "@live_curve");
    Schwarz (#000000, frueher die Live-Kurve) = live_curve (im Nachtmodus weiss)."""
    if not basis:
        return basis
    T = THEMES[_thema(thema)]
    if basis.startswith("@"):
        return T.get(basis[1:], T["live_curve"])
    if basis.lower() in ("#000000", "#000", "black"):
        return T["live_curve"]
    return anpassen(basis, T["plot"], LAUF_KONTRAST)


def kurvenfarbe(basis: str, thema: Optional[str] = None) -> str:
    """Wie laufarbe, fuer feste Kanalfarben (Oszi, Auswertung): auf beiden Diagrammflaechen sichtbar."""
    return laufarbe(basis, thema)


def status_farben(stufe: str, thema: Optional[str] = None) -> Tuple[str, str, str]:
    """(Hintergrund, Schrift, Symbol) einer Zustandsflaeche; Stufe ok | warn | err | aus."""
    bg, fg, sym = _STATUS.get(stufe, _STATUS[AUS])
    T = THEMES[_thema(thema)]
    return T[bg], T[fg], sym


def stufe_schrift(stufe: str, thema: Optional[str] = None) -> str:
    """Schriftfarbe fuer Zustandstext auf Fenster/Kachel (nicht auf farbiger Flaeche)."""
    T = THEMES[_thema(thema)]
    return T.get({GUT: "ok_fg", WARNUNG: "warn_fg", FEHLER: "err_fg", "info": "info_fg"}.get(stufe, "text"), T["text"])


def system_thema() -> str:
    """Farbschema des Systems: "nacht" bei dunklem Schema, sonst "tag" (auch wenn unbekannt)."""
    app = QtGui.QGuiApplication.instance()
    try:
        scheme = app.styleHints().colorScheme() if app else None
    except AttributeError:                         # Qt < 6.5
        return TAG
    return NACHT if scheme == QtCore.Qt.ColorScheme.Dark else TAG


def aktiv() -> str:
    """Aktives Thema ("tag" oder "nacht")."""
    return _MANAGER.aktiv if _MANAGER is not None else TAG


def ist_nacht() -> bool:
    return aktiv() == NACHT


# =============================================================================================== Palette, Stylesheet
def palette(thema: str) -> QtGui.QPalette:
    T = THEMES[thema]
    p = QtGui.QPalette()
    c = QtGui.QColor
    P = QtGui.QPalette
    for role, tok in ((P.Window, "win"), (P.WindowText, "text"), (P.Base, "field"), (P.AlternateBase, "alt_row"),
                      (P.Text, "input_text"), (P.Button, "panel"), (P.ButtonText, "text"), (P.ToolTipBase, "menu"),
                      (P.ToolTipText, "text"), (P.Highlight, "sel_bg"), (P.HighlightedText, "accent_text"),
                      (P.PlaceholderText, "text2"), (P.Mid, "border"), (P.Dark, "border"), (P.Light, "panel"),
                      (P.Midlight, "panel"), (P.Shadow, "border_strong"), (P.BrightText, "value"),
                      (P.Link, "info_fg"), (P.LinkVisited, "info_fg")):
        p.setColor(role, c(T[tok]))
    for role in (P.Text, P.ButtonText, P.WindowText):
        p.setColor(P.Disabled, role, c(T["dim"]))
    return p


def _haken_datei() -> str:
    """Haken fuer angekreuzte Kaestchen (weiss auf Akzentfarbe, in beiden Themen gleich)."""
    path = os.path.join(os.path.dirname(__file__), "data", "haken.svg")
    return path.replace("\\", "/")


_PFEIL_PUNKTE = {"hoch": "1,7 5,2 9,7", "runter": "1,3 5,8 9,3"}


@lru_cache(maxsize=None)
def _pfeil_datei(richtung: str, farbe: str) -> str:
    """Kleines Dreieck (SVG) in der Themenfarbe fuer Drehfelder und Auswahllisten; liegt im Temp-Ordner,
    weil Qt-Stylesheets nur Dateipfade als Bild kennen."""
    ordner = os.path.join(tempfile.gettempdir(), "pyst_thema")
    os.makedirs(ordner, exist_ok=True)
    path = os.path.join(ordner, f"pfeil_{richtung}_{farbe.lstrip('#')}.svg")
    if not os.path.exists(path):
        with open(path, "w", encoding="ascii") as f:
            f.write('<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 10 10">'
                    f'<polygon points="{_PFEIL_PUNKTE[richtung]}" fill="{farbe}"/></svg>')
    return path.replace("\\", "/")


def stylesheet(thema: str) -> str:
    T = THEMES[thema]
    haken = _haken_datei()
    hoch, runter = _pfeil_datei("hoch", T["text2"]), _pfeil_datei("runter", T["text2"])
    hoch_aus, runter_aus = _pfeil_datei("hoch", T["dim"]), _pfeil_datei("runter", T["dim"])
    ind = (f"width: 15px; height: 15px; border: 1px solid {T['border_strong']}; border-radius: 3px;"
           f" background: {T['field']};")
    ind_an = f"background: {T['accent']}; border-color: {T['accent']}; image: url({haken});"
    return f"""
        QPushButton {{ background: {T['panel']}; color: {T['text']}; border: 1px solid {T['border']};
                       border-radius: 4px; padding: 3px 8px; min-height: 20px; }}
        QPushButton:hover {{ border-color: {T['accent_border']}; }}
        QPushButton:pressed {{ background: {T['tab_sel']}; }}
        QPushButton:checked {{ background: {T['accent']}; color: {T['accent_text']}; border-color: {T['accent']}; }}
        QPushButton:disabled {{ color: {T['dim']}; border-color: {T['border']}; }}
        QPushButton[rolle="primaer"] {{ background: {T['accent']}; color: {T['accent_text']}; font-weight: 700;
                       border: 1px solid {T['accent']}; }}
        QPushButton[rolle="primaer"]:disabled {{ background: {T['panel']}; color: {T['dim']}; border-color: {T['border']}; }}
        QToolButton {{ color: {T['text']}; }}
        QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QDateEdit, QTimeEdit {{ background: {T['field']};
                       color: {T['input_text']}; border: 1px solid {T['border']}; border-radius: 3px;
                       padding: 2px 4px; min-height: 18px; selection-background-color: {T['sel_bg']};
                       selection-color: {T['accent_text']}; }}
        QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {{ color: {T['dim']}; }}
        QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{ border-color: {T['accent_border']}; }}
        QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{ width: 16px; border: none;
                       border-left: 1px solid {T['border']}; background: {T['panel']}; }}
        QAbstractSpinBox::up-button {{ subcontrol-origin: border; subcontrol-position: top right; }}
        QAbstractSpinBox::down-button {{ subcontrol-origin: border; subcontrol-position: bottom right; }}
        QAbstractSpinBox::up-arrow {{ image: url({hoch}); width: 8px; height: 8px; }}
        QAbstractSpinBox::down-arrow {{ image: url({runter}); width: 8px; height: 8px; }}
        QAbstractSpinBox::up-arrow:disabled {{ image: url({hoch_aus}); }}
        QAbstractSpinBox::down-arrow:disabled {{ image: url({runter_aus}); }}
        QComboBox::drop-down {{ width: 18px; border: none; border-left: 1px solid {T['border']}; }}
        QComboBox::down-arrow {{ image: url({runter}); width: 9px; height: 9px; }}
        QComboBox::down-arrow:disabled {{ image: url({runter_aus}); }}
        QComboBox QAbstractItemView {{ background: {T['menu']}; color: {T['text']};
                       selection-background-color: {T['sel_bg']}; selection-color: {T['accent_text']}; }}
        QPlainTextEdit, QTextEdit, QTextBrowser {{ background: {T['field']}; color: {T['input_text']};
                       border: 1px solid {T['border']}; selection-background-color: {T['sel_bg']};
                       selection-color: {T['accent_text']}; }}
        QCheckBox, QRadioButton {{ color: {T['text']}; }}
        QCheckBox:disabled, QRadioButton:disabled {{ color: {T['dim']}; }}
        QCheckBox::indicator {{ {ind} }}
        QCheckBox::indicator:checked {{ {ind_an} }}
        QCheckBox::indicator:disabled {{ border-color: {T['border']}; background: {T['win']}; }}
        QTabWidget::pane {{ border: 1px solid {T['border']}; top: -1px; }}
        QTabBar::tab {{ background: {T['win']}; color: {T['text2']}; border: 1px solid {T['border']};
                       border-bottom: none; padding: 5px 14px; margin-right: 2px;
                       border-top-left-radius: 4px; border-top-right-radius: 4px; }}
        QTabBar::tab:selected {{ background: {T['tab_sel']}; color: {T['text']}; font-weight: 700;
                       border-top: 3px solid {T['accent_border']}; }}
        QTabBar::tab:hover:!selected {{ color: {T['text']}; }}
        QHeaderView::section {{ background: {T['panel']}; color: {T['text2']}; border: none;
                       border-bottom: 1px solid {T['border']}; border-right: 1px solid {T['plot_grid']};
                       padding: 2px 4px; font-weight: 700; }}
        QTreeView, QListView, QTableView {{ background: {T['field']}; color: {T['input_text']};
                       border: 1px solid {T['border']}; alternate-background-color: {T['alt_row']};
                       gridline-color: {T['plot_grid']}; selection-background-color: {T['sel_bg']};
                       selection-color: {T['accent_text']}; }}
        QTreeView::indicator, QListView::indicator, QTableView::indicator {{ {ind} }}
        QTreeView::indicator:checked, QListView::indicator:checked, QTableView::indicator:checked {{ {ind_an} }}
        QTableCornerButton::section {{ background: {T['panel']}; border: none; }}
        QGroupBox {{ background: {T['panel']}; border: 1px solid {T['border']}; border-radius: 5px;
                       margin-top: 14px; padding: 8px 6px 6px 6px; color: {T['text']}; }}
        QGroupBox::title {{ subcontrol-origin: margin; left: 8px; padding: 0 4px; color: {T['text']}; }}
        QScrollArea {{ background: transparent; border: none; }}
        QMenuBar {{ background: {T['win']}; color: {T['text']}; }}
        QMenuBar::item:selected {{ background: {T['sel_bg']}; color: {T['accent_text']}; }}
        QMenu {{ background: {T['menu']}; color: {T['text']}; border: 1px solid {T['border_strong']}; }}
        QMenu::item:selected {{ background: {T['sel_bg']}; color: {T['accent_text']}; }}
        QMenu::item:disabled {{ color: {T['dim']}; }}
        QMenu::separator {{ height: 1px; background: {T['border']}; margin: 3px 6px; }}
        QToolTip {{ background: {T['menu']}; color: {T['text']}; border: 1px solid {T['border_strong']}; }}
        QStatusBar {{ background: {T['win']}; color: {T['text2']}; border-top: 1px solid {T['border']}; }}
        QStatusBar::item {{ border: none; }}
        QProgressBar {{ background: {T['track']}; border: 1px solid {T['border']}; border-radius: 3px;
                       color: {T['text']}; text-align: center; }}
        QProgressBar::chunk {{ background: {T['zone_ok']}; border-radius: 2px; }}
        QSplitter::handle {{ background: {T['win']}; }}
        QLabel[rolle="neben"] {{ color: {T['text2']}; }}
        QLabel[rolle="dim"] {{ color: {T['dim']}; }}
        QLabel[rolle="titel"] {{ color: {T['text']}; font-weight: 700; }}
        QLabel[rolle="abschnitt"] {{ color: {T['text2']}; font-size: 12px; font-weight: 700; letter-spacing: 1px; }}
        QLabel[rolle="banner_fehler"] {{ background: {T['err_bg']}; color: {T['err_text']}; font-weight: 700;
                       padding: 6px; border-radius: 4px; }}
        QLabel[rolle="banner_warnung"] {{ background: {T['warn_bg']}; color: {T['warn_text']}; font-weight: 700;
                       padding: 6px; border-radius: 4px; }}
        QLabel[stufe="ok"] {{ color: {T['ok_fg']}; }}
        QLabel[stufe="warn"] {{ color: {T['warn_fg']}; }}
        QLabel[stufe="err"] {{ color: {T['err_fg']}; }}
        QLabel[stufe="info"] {{ color: {T['info_fg']}; }}
        QLabel[stufe="neben"] {{ color: {T['text2']}; }}
        QToolButton[rolle="klein"] {{ color: {T['text2']}; font-size: 12px; padding: 0px 4px; border: none; }}
        QToolButton[rolle="klein"]:hover {{ color: {T['text']}; }}
    """


# =============================================================================================== Wechsel
class ThemeManager(QtCore.QObject):
    """Haelt Wahl (tag/nacht/auto) und aktives Thema, wendet Palette/Stylesheet auf die Anwendung an."""
    geaendert = QtCore.Signal(str)          # aktives Thema nach dem Wechsel ("tag"/"nacht")
    wahl_geaendert = QtCore.Signal(str)     # Wahl des Anwenders ("tag"/"nacht"/"auto") – zum Speichern

    def __init__(self):
        super().__init__()
        self.wahl = STANDARD_WAHL
        self.aktiv = TAG
        self._angewendet = ""
        self._system_verbunden = False

    def setze(self, wahl: str, speichern: bool = True):
        """Wahl setzen und anwenden. speichern=False: wahl_geaendert nicht senden (z.B. beim Start)."""
        wahl = wahl if wahl in WAHLEN else STANDARD_WAHL
        alt = self.wahl
        self.wahl = wahl
        self._verbinde_system()
        self.anwenden()
        if speichern and wahl != alt:
            self.wahl_geaendert.emit(wahl)

    def _verbinde_system(self):
        app = QtGui.QGuiApplication.instance()
        if self._system_verbunden or app is None:
            return
        try:
            app.styleHints().colorSchemeChanged.connect(self._system_wechsel)
            self._system_verbunden = True
        except AttributeError:
            pass

    def _system_wechsel(self, *_a):
        if self.wahl == AUTO:
            self.anwenden()

    def anwenden(self, erzwingen: bool = False):
        self.aktiv = system_thema() if self.wahl == AUTO else self.wahl
        app = QtWidgets.QApplication.instance()
        if app is not None and (erzwingen or self._angewendet != self.aktiv):
            app.setStyle("Fusion")
            f = QtGui.QFont(app.font())
            f.setPixelSize(SCHRIFT_PX)
            app.setFont(f)
            app.setPalette(palette(self.aktiv))
            app.setStyleSheet(stylesheet(self.aktiv))
            self._angewendet = self.aktiv
            self.geaendert.emit(self.aktiv)


_MANAGER: Optional[ThemeManager] = None


def manager() -> ThemeManager:
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = ThemeManager()
    return _MANAGER


def setze(wahl: str, speichern: bool = True):
    manager().setze(wahl, speichern)


def verbinde(signal, widget: QtCore.QObject, fn: Callable):
    """signal -> fn, solange widget lebt (danach getrennt; ein schon geloeschtes widget wird nie aufgerufen)."""
    import shiboken6

    def slot(*args):
        if shiboken6.isValid(widget):
            fn(*args)
    signal.connect(slot)

    def weg(*_a):
        try:
            signal.disconnect(slot)
        except (RuntimeError, TypeError, SystemError):
            pass
    widget.destroyed.connect(weg)
    return slot


def bei_wechsel(widget: QtCore.QObject, fn: Callable[[str], None], sofort: bool = True):
    """fn(thema) sofort und nach jedem Themenwechsel aufrufen, solange widget lebt."""
    verbinde(manager().geaendert, widget, fn)
    if sofort:
        fn(aktiv())


def _neu_polieren(w: QtWidgets.QWidget):
    st = w.style()
    st.unpolish(w)
    st.polish(w)
    w.update()


def rolle(widget: QtWidgets.QWidget, name: str) -> QtWidgets.QWidget:
    """Textrolle setzen: neben (Nebenschrift), dim, titel, abschnitt, banner_fehler, banner_warnung,
    primaer (Knopf), klein (kleiner Werkzeugknopf). Die Farbe kommt aus dem Stylesheet der Anwendung."""
    widget.setProperty("rolle", name)
    _neu_polieren(widget)
    return widget


def stufe(widget: QtWidgets.QWidget, name: str) -> QtWidgets.QWidget:
    """Zustandsfarbe der Schrift: ok | warn | err | info | "" (normal)."""
    widget.setProperty("stufe", name or "")
    _neu_polieren(widget)
    return widget


# =============================================================================================== Diagramme (pyqtgraph)
def achsen_schrift() -> QtGui.QFont:
    f = QtGui.QFont()
    f.setPixelSize(ACHSE_PX)
    return f


def plotitem_stil(pi, thema: Optional[str] = None):
    """Rahmen, Achsen, Beschriftung und Titel eines pyqtgraph-PlotItems im Thema. Achstitel bekommen die
    Achsenfarbe; wer farbige Achstitel will (Leistung/Drehmoment …), setzt sie danach neu."""
    import pyqtgraph as pg
    T = THEMES[_thema(thema)]
    f = achsen_schrift()
    for name in ("left", "right", "bottom", "top"):
        ax = pi.getAxis(name)
        ax.setPen(pg.mkPen(T["plot_frame"]))
        ax.setTextPen(pg.mkPen(T["plot_axis"]))
        ax.setTickFont(f)
    title = getattr(pi, "titleLabel", None)
    if title is not None and title.text:
        title.setText(title.text, color=T["text"])
    legend = getattr(pi, "legend", None)
    if legend is not None:
        legend.setLabelTextColor(T["text"])
        c = QtGui.QColor(T["panel"])
        c.setAlpha(210)
        legend.setBrush(c)
        legend.setPen(pg.mkPen(T["border"]))


def diagramm(widget, nachher: Optional[Callable[[str], None]] = None):
    """pg.PlotWidget oder pg.GraphicsLayoutWidget im Thema darstellen, auch nach jedem Wechsel:
    Hintergrund = Diagrammflaeche, alle PlotItems mit plotitem_stil. nachher(thema) danach (eigene Farben)."""
    import pyqtgraph as pg

    def anwenden(th: str):
        widget.setBackground(THEMES[th]["plot"])
        if isinstance(widget, pg.PlotWidget):
            items = [widget.getPlotItem()]
        else:
            ci = getattr(widget, "ci", None)
            items = [it for it in (ci.items if ci is not None else {}) if isinstance(it, pg.PlotItem)]
        for pi in items:
            plotitem_stil(pi, th)
        if nachher is not None:
            nachher(th)
    bei_wechsel(widget, anwenden)
