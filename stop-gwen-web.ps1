$ErrorActionPreference = "Stop"

$projectRoot = $PSScriptRoot
$pidPath = Join-Path $projectRoot ".gwen-web.pid"
if (-not (Test-Path -LiteralPath $pidPath)) {
    Write-Host "La interfaz web no está activa."
    exit 0
}
$pythonPath = (Resolve-Path -LiteralPath (Join-Path $projectRoot ".venv\Scripts\python.exe")).Path
$savedPid = (Get-Content -Raw -LiteralPath $pidPath).Trim()
if ($savedPid -notmatch '^\d+$') { throw "El PID de la interfaz web no es válido." }
$process = Get-Process -Id ([int]$savedPid) -ErrorAction SilentlyContinue
if (-not $process) {
    Remove-Item -LiteralPath $pidPath -Force
    Write-Host "La interfaz web ya estaba detenida."
    exit 0
}
if ($process.Path -ne $pythonPath) { throw "El PID pertenece a otro programa; no se detuvo." }
Stop-Process -Id $process.Id
Wait-Process -Id $process.Id -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $pidPath -Force
Write-Host "Interfaz web detenida."
