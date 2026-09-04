$ErrorActionPreference = "Stop"

$projectRoot = $PSScriptRoot
$pidPath = Join-Path $projectRoot ".gwen.pid"

if (-not (Test-Path -LiteralPath $pidPath)) {
    Write-Host "Gwen no está activa."
    exit 0
}

$pythonPath = (Resolve-Path -LiteralPath (Join-Path $projectRoot ".venv\Scripts\python.exe")).Path
$savedPid = (Get-Content -Raw -LiteralPath $pidPath).Trim()
if ($savedPid -notmatch '^\d+$') {
    throw "El archivo .gwen.pid no contiene un PID válido; no se detuvo ningún proceso."
}

$process = Get-Process -Id ([int]$savedPid) -ErrorAction SilentlyContinue
if (-not $process) {
    Remove-Item -LiteralPath $pidPath -Force
    Write-Host "Gwen ya estaba detenida; se eliminó el PID obsoleto."
    exit 0
}

if ($process.Path -ne $pythonPath) {
    throw "El PID guardado pertenece a otro programa; no se detuvo ningún proceso."
}

Stop-Process -Id $process.Id
Wait-Process -Id $process.Id -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $pidPath -Force
Write-Host "Gwen se detuvo."
