@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\pythonw.exe (
    echo Richte Python-Umgebung ein ...
    python -m venv .venv
    .venv\Scripts\python.exe -m pip install -q meshtastic pystray pillow
)
start "" .venv\Scripts\pythonw.exe mesh_responder.py %*
