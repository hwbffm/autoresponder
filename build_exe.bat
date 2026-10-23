@echo off
rem Baut MeshResponder.exe (eine einzelne Datei) mit PyInstaller. Public Domain (Unlicense).
rem Voraussetzung: Python im PATH. Ergebnis: dist\MeshResponder.exe
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    echo Richte Python-Umgebung ein ...
    python -m venv .venv
    .venv\Scripts\python.exe -m pip install -q meshtastic pystray pillow
)
.venv\Scripts\python.exe -m pip install -q pyinstaller
.venv\Scripts\python.exe make_icon.py
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --onefile --noconsole ^
    --name MeshResponder --icon mesh_responder.ico ^
    --hidden-import pystray._win32 ^
    --collect-submodules pubsub ^
    --collect-all meshtastic ^
    mesh_responder.py
echo.
if exist dist\MeshResponder.exe (echo Fertig: %~dp0dist\MeshResponder.exe) else (echo FEHLER: keine MeshResponder.exe erzeugt)
