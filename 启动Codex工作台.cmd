@echo off
setlocal
cd /d "%~dp0"
set "studioPython=python"
if exist ".venv-workbench\Scripts\python.exe" set "studioPython=%CD%\.venv-workbench\Scripts\python.exe"
"%studioPython%" -c "import sys, yaml; assert sys.version_info >= (3,11), 'Python 3.11+ required'"
if errorlevel 1 (
    echo Python 3.11+ and PyYAML are required. See README.md for setup.
    pause
    exit /b 1
)
"%studioPython%" -B workbench\start_codex.py
if errorlevel 1 pause
