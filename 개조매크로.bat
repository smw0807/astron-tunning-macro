@echo off
cd /d "%~dp0"
python -m src.gui
if errorlevel 1 pause
