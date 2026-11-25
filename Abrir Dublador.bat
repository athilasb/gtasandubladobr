@echo off
cd /d "%~dp0"
set PY=py -3.11
%PY% -c "import sys" 2>nul || set PY=python
%PY% -c "import numpy, scipy, soundfile, sounddevice" 2>nul || (
  echo Instalando bibliotecas necessarias...
  %PY% -m pip install numpy scipy soundfile sounddevice
)
%PY% dublador.py
if errorlevel 1 pause
