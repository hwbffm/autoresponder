@echo off
rem Startet den Keepalive-Proxy ohne Konsolenfenster (siehe Anleitung 4b).
rem Eigene Adressen: start_keepalive_proxy.bat --node 192.168.10.41:4403 --listen 127.0.0.1:4405 --interval 60
cd /d "%~dp0"
if not exist .venv\Scripts\pythonw.exe (
    echo Richte Python-Umgebung ein ...
    python -m venv .venv
    .venv\Scripts\python.exe -m pip install -q meshtastic pystray pillow
)
start "" .venv\Scripts\pythonw.exe keepalive_proxy.py %*
