$ErrorActionPreference = "Stop"

$projectRoot = $PSScriptRoot
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "No se encontró .venv. Crea el entorno e instala el proyecto primero."
}

& $pythonPath -m gwen.backup
