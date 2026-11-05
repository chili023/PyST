"""Geladene Laeufe im Speicher: begrenzt nach Anzahl und Groesse, der zuletzt benutzte bleibt (Raspberry Pi, lange
Dauermessungen). Ohne Qt."""
from collections import OrderedDict
from typing import Any, Optional

import numpy as np

MAX_ENTRIES = 8
MAX_BYTES = 300_000_000


def nbytes(obj: Any) -> int:
    """Speicher der numpy-Felder in Dicts, Listen, Tupeln und Objekten. Mehrfach erreichbare Felder zaehlen einmal."""
    seen = set()

    def walk(o) -> int:
        if id(o) in seen:
            return 0
        seen.add(id(o))
        if isinstance(o, np.ndarray):
            return o.nbytes
        if isinstance(o, dict):
            return sum(walk(v) for v in o.values())
        if isinstance(o, (list, tuple)):
            return sum(walk(v) for v in o)
        if hasattr(o, "__dict__"):
            return walk(vars(o))
        return 0

    return walk(obj)


class RunCache:
    def __init__(self, max_entries: int = MAX_ENTRIES, max_bytes: int = MAX_BYTES):
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self.items: "OrderedDict[int, Any]" = OrderedDict()
        self.sizes = {}

    def __contains__(self, key) -> bool:
        return key in self.items

    def __len__(self) -> int:
        return len(self.items)

    @property
    def total(self) -> int:
        return sum(self.sizes.values())

    def get(self, key) -> Optional[Any]:
        if key not in self.items:
            return None
        self.items.move_to_end(key)
        return self.items[key]

    def put(self, key, value):
        self.items[key] = value
        self.items.move_to_end(key)
        self.sizes[key] = nbytes(value)
        while len(self.items) > 1 and (len(self.items) > self.max_entries or self.total > self.max_bytes):
            old, _v = self.items.popitem(last=False)
            del self.sizes[old]

    def pop(self, key, default=None):
        self.sizes.pop(key, None)
        return self.items.pop(key, default)
