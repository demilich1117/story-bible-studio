$ErrorActionPreference = 'Stop'
$studioRoot = $PSScriptRoot
$studioPython = Join-Path $studioRoot '.venv-workbench\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $studioPython)) {
    python -m venv (Join-Path $studioRoot '.venv-workbench')
    if ($LASTEXITCODE -ne 0) { throw 'Cannot create Python virtual environment.' }
}
& $studioPython -m pip install -r (Join-Path $studioRoot 'workbench\requirements-mcp.txt')
if ($LASTEXITCODE -ne 0) { throw 'MCP dependency installation failed; existing platform settings were not changed.' }
& $studioPython -c "import mcp; import yaml; print('MCP dependencies are ready. Copy a platform configuration from the workbench.')"
