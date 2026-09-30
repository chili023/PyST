#!/bin/bash
# Doppelklick im Finder: richtet beim ersten Mal alles ein und startet PyST.
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "Erster Start: Python-Umgebung wird eingerichtet (einmalig, ca. 1-2 Minuten) ..."
  python3 -m venv .venv && ./.venv/bin/pip install -q --upgrade pip && ./.venv/bin/pip install -q -r requirements.txt || exit 1
fi
exec ./.venv/bin/python -m pyst "$@"
