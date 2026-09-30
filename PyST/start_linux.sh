#!/bin/bash
# Raspberry Pi / Linux: richtet beim ersten Mal alles ein und startet PyST.
#   sudo apt install python3-venv          (falls noch nicht vorhanden)
#   sudo usermod -aG dialout $USER         (Zugriff auf /dev/ttyACM0, danach neu anmelden)
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "Erster Start: Python-Umgebung wird eingerichtet ..."
  python3 -m venv .venv && ./.venv/bin/pip install -q --upgrade pip && ./.venv/bin/pip install -q -r requirements.txt || exit 1
fi
exec ./.venv/bin/python -m pyst "$@"
