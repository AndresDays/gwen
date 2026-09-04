$ErrorActionPreference = "Stop"

$projectRoot = $PSScriptRoot
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
$pidPath = Join-Path $projectRoot ".gwen-web.pid"
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "No se encontró .venv. Crea el entorno e instala el proyecto primero."
}
if (Test-Path -LiteralPath $pidPath) {
    $savedPid = (Get-Content -Raw -LiteralPath $pidPath).Trim()
    if ($savedPid -match '^\d+$' -and (Get-Process -Id ([int]$savedPid) -ErrorAction SilentlyContinue)) {
        Write-Host "La interfaz web ya está activa en http://127.0.0.1:8765"
        exit 0
    }
    Remove-Item -LiteralPath $pidPath -Force
}
$process = Start-Process -FilePath $pythonPath -ArgumentList "-m", "gwen.web" `
    -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru
Set-Content -LiteralPath $pidPath -Value $process.Id -Encoding ascii
Write-Host "Interfaz web iniciada: http://127.0.0.1:8765 (PID $($process.Id))"
