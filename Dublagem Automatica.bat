@echo off
cd /d "%~dp0"
echo Dublagem automatica para portugues (VoiceStudio precisa estar aberto).
echo Pode fechar esta janela a qualquer momento: ao abrir de novo, continua de onde parou.
echo.
py -3.11 -c "import requests" 2>nul || py -3.11 -m pip install requests
py -3.11 dublagem_auto.py %*
pause
