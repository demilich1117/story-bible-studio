@echo off
cd /d "%~dp0"
set "studioPython=python"
if exist ".venv-workbench\Scripts\python.exe" set "studioPython=%CD%\.venv-workbench\Scripts\python.exe"
"%studioPython%" -B workbench\opencode_server.py serve
if errorlevel 1 pause
