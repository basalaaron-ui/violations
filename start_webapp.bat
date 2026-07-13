@echo off
rem Start the PropFolio website with live updates + property editing.
cd /d "%~dp0"
start "" http://localhost:8642
python webapp\server.py
pause
