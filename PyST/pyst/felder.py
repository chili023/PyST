"""Zeilen der Geraete lesen: Schluessel=Wert-Felder und Zahlen, die auch kaputt sein duerfen."""
from typing import Dict


def parse_kv(text: str) -> Dict[str, str]:
    """'#BURST;BEGIN;n=800;tick_ns=62.5' -> {'n': '800', 'tick_ns': '62.5'} (Felder ohne '=' werden ignoriert)."""
    out = {}
    for part in text.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def to_float(v, default: float = float("nan")) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def to_int(v, default: int = 0) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError, OverflowError):       # 'inf' laesst sich nicht wandeln
        return default
