"""Zustandsfarben der Oberflaeche – kommen aus dem aktiven Thema (theme.py), Kurvenfarben stehen bei den Kurven.

farben.GUT / WARNUNG / FEHLER / INFO / NEUTRAL werden bei jedem Zugriff aus dem aktiven Thema gelesen
(Schriftfarbe auf Fenster/Kachel, Kontrast >= 4,5:1). Fuer farbige Flaechen theme.status_farben(stufe) nehmen.
BALKEN_FEHLER / BALKEN_NORMAL sind Stufen fuer Zustandsflaechen (frueher feste Farben der Statuszeile).
"""
from . import theme

BALKEN_FEHLER = theme.FEHLER            # Stufe "err"
BALKEN_NORMAL = ""                      # neutrale Flaeche (Kachel)

_TOKEN = {"GUT": "ok_fg", "WARNUNG": "warn_fg", "FEHLER": "err_fg", "INFO": "info_fg", "NEUTRAL": "text2"}
_STUFE = {"GUT": theme.GUT, "WARNUNG": theme.WARNUNG, "FEHLER": theme.FEHLER, "INFO": "info"}

# feste Farben aelterer Aufrufer (Zustandszeile Messen) -> Stufe
_ALT = {"#7ed67e": theme.GUT, "#b8e6b8": theme.GUT, "#2e7d32": theme.GUT, "#ffe08a": theme.WARNUNG,
        "#ef6c00": theme.WARNUNG, "#ff5555": theme.FEHLER, "#f4b6b6": theme.FEHLER, "#c62828": theme.FEHLER,
        "#9fc9ff": "info", "#1f5fbf": "info", "#dddddd": ""}


def __getattr__(name: str) -> str:
    tok = _TOKEN.get(name)
    if tok is None:
        raise AttributeError(name)
    return theme.farbe(tok)


def stufe_von(farbe: str) -> str:
    """Stufe (ok | warn | err | info | "") zu einer Farbe aus farben.* (beide Themen) oder einer alten festen Farbe."""
    if not farbe:
        return ""
    f = farbe.lower()
    if f in (theme.GUT, theme.WARNUNG, theme.FEHLER, theme.AUS, "info"):
        return f
    for name, tok in _TOKEN.items():
        if name in _STUFE and any(T[tok].lower() == f for T in theme.THEMES.values()):
            return _STUFE[name]
    return _ALT.get(f, "")
