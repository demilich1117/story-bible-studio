@echo off
cd /d "%~dp0"
set "studioPython=python"
if exist ".venv-workbench\Scripts\python.exe" set "studioPython=%CD%\.venv-workbench\Scripts\python.exe"
"%studioPython%" -c "import sys, yaml; assert sys.version_info >= (3,11), 'Python 3.11+ required'"
if errorlevel 1 (
    echo Python 3.11+ and PyYAML are required. Run the MCP installer or: python -m pip install -r requirements.txt
    pause
    exit /b 1
)
"%studioPython%" -B workbench\server.py --open
if errorlevel 1 pause
