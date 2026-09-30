@echo off
rem PyST fuer Windows: beim ersten Start wird die Python-Umgebung eingerichtet.
cd /d "%~dp0"
if exist .venv\Scripts\python.exe goto run
echo Erster Start: Python-Umgebung wird eingerichtet (einmalig, 1-2 Minuten) ...
py -3 -m venv .venv >NUL 2>&1
if not exist .venv\Scripts\python.exe python -m venv .venv
if not exist .venv\Scripts\python.exe (
  echo Python 3 nicht gefunden. Bitte von https://www.python.org installieren
  echo und beim Installieren "Add python.exe to PATH" anhaken.
  pause
  exit /b 1
)
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -r requirements.txt
if errorlevel 1 (
  echo Installation fehlgeschlagen.
  pause
  exit /b 1
)
:run
.venv\Scripts\python -m pyst %*
